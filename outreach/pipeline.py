"""
The deterministic spine of the pipeline.

Stages 1, 2, 4, 6 and 8 are all here. Stages 3 and 5 are not, and cannot
be: they are agent calls, driven by the `outreach` skill from a Claude Code
session. This module is what that session calls before and after each of
them, so every step with a lasting consequence — what gets contacted,
whether an address is real, what lands in Gmail, what is recorded — runs as
ordinary code that can be read and tested, not as a model's decision.

    prepare()   stages 1-2 + dedup   -> companies worth an Agent 1 call
      [ agent 1: find the contact slate ]
    resolve_candidate_slate()        -> which of them are reachable
      [ human picks; agent 2 writes the draft ]
    finalize()  stages 4, 6, 8      -> verify, draft into Gmail, claim
    bump_candidates() / create_bump() -> the one permitted follow-up
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from qualify.boards import job_data_for
from qualify.candidates import fetch_candidates
from qualify.eligibility import check_citizenship_required, check_title, check_years
from qualify.extractor import heuristic_extract_requirements
from qualify.semantic import cached_score

from outreach import store
from outreach.draft_lint import lint, lint_linkedin
from outreach.gmail_draft import create_draft, create_reply_draft, resume_path
from outreach.history import prior_contacts
from outreach.verify import DEFERRED, resolve_address, resolve_slate, verify_email

# The score IS the judge's 0-100 since 2026-08-26 (docs/qualify.md, "The
# judge becomes the score"). 65 is the judge scale's own boundary between
# "real fit with some distance" and "adjacent — not where his background
# is an advantage": an adjacent posting is not worth a cold email whose
# whole premise is a specific story, and both ground-truth rejections sat
# below this line (55, 58). Must stay consistent with TIERS in
# qualify_run.py.
DEFAULT_MIN_SCORE = 65


@dataclass
class Candidate:
    company_slug: str
    platform: str
    job_id: str
    job_title: str
    job_url: str | None
    score: int
    judge_reason: str | None
    funding_hint: str | None
    description_text: str = field(repr=False, default="")


def _judge_row(row: dict) -> tuple[int | None, str | None, dict] | None:
    """(score, reason, job_data) for one eligible posting.

    None when the posting is ineligible or gone from its board;
    (None, None, job_data) when it is eligible but not yet judged — the
    caller surfaces those rather than ranking them, because an unjudged
    posting has no score at all now, not a partial one.
    """
    job_data = job_data_for(row)
    if job_data is None:
        return None
    reqs = heuristic_extract_requirements(job_data["description_text"])
    if not check_years(reqs.get("years_experience_min"))[0]:
        return None
    if not check_citizenship_required(reqs.get("citizenship_required"))[0]:
        return None
    judged = cached_score(row["platform"], row["job_id"])
    if judged is None:
        return None, None, job_data
    return judged["score"], judged.get("reason"), job_data


def prepare(
    *, days: int = 1, limit: int | None = None, min_score: int = DEFAULT_MIN_SCORE
) -> tuple[list[Candidate], list[str]]:
    """Stages 1-2 plus the dedup gate.

    Returns (candidates, skipped) where candidates are one-per-company, best
    posting first, with every already-claimed company removed. A company
    that already has a pending draft has that draft repointed at the better
    posting instead of producing a second one — no second contact lookup,
    per PIPELINE.md's rate-limiting policy.
    """
    rows = fetch_candidates(days=days, limit=limit)
    skipped: list[str] = []

    best: dict[str, Candidate] = {}
    unjudged = 0
    for row in rows:
        # Eligibility before judging: an internship or a frontend-titled
        # role is not a weak match, it is not a match. No point paying for
        # a board fetch to rank something that cannot be taken.
        eligible, reason = check_title(row.get("title") or "")
        if not eligible:
            continue
        judged = _judge_row(row)
        if judged is None:
            continue
        total, judge_reason, job_data = judged
        if total is None:
            unjudged += 1
            continue
        if total < min_score:
            continue
        slug = row["company_slug"]
        existing = best.get(slug)
        if existing and existing.score >= total:
            continue
        best[slug] = Candidate(
            company_slug=slug,
            platform=row["platform"],
            job_id=str(row["job_id"]),
            job_title=job_data["title"],
            job_url=row.get("url"),
            score=total,
            judge_reason=judge_reason,
            funding_hint=row.get("funding_hint"),
            description_text=job_data["description_text"],
        )
    if unjudged:
        skipped.append(
            f"{unjudged} eligible posting(s) not judged yet — they have no "
            f"score and were not ranked; run judge/judge-save on this window"
        )

    out: list[Candidate] = []
    for slug, cand in best.items():
        state, existing = store.claim_state(slug)
        if state == "sent":
            skipped.append(f"{slug}: already contacted {existing['sent_at']}")
            continue
        if state is not None and state != store.DISCARDED:
            swapped = store.update_posting(
                company_slug=slug, platform=cand.platform, job_id=cand.job_id,
                job_title=cand.job_title, job_url=cand.job_url, score=cand.score,
            )
            note = ("noted a higher-scoring posting for review"
                    if swapped else "no better posting since")
            skipped.append(f"{slug}: draft already pending, {note}")
            continue
        out.append(cand)

    out.sort(key=lambda c: c.score, reverse=True)
    return out, skipped


def resolve_candidate_slate(
    domain: str, candidates: list[dict], *, observed_address: str | None = None
) -> list[dict]:
    """Resolve reachability for Agent 1's ranked slate, before the human
    picks and before anything is drafted.

    One cached domain-search covers all candidates; a verification credit
    is spent only until the first deliverable address (outreach/verify.py,
    resolve_slate). This is advisory — finalize() re-resolves whichever
    candidate is actually chosen, and its result is the one that binds.
    DEFERRED (resolvable, deliberately unverified) is exposed as an empty
    label with the reason carrying the explanation, so no consumer needs
    to know the constant.
    """
    names = [c.get("name") or "" for c in candidates]
    resolutions = resolve_slate(names, domain, fallback=observed_address)
    out = []
    for cand, res in zip(candidates, resolutions):
        merged = dict(cand)
        merged.update({
            "address": res.address,
            "address_source": res.source,
            "verify_label": "" if res.label == DEFERRED else res.label,
            "verify_score": res.score,
            "verify_reason": res.reason,
        })
        out.append(merged)
    return out


# The follow-up window, in business days since send. Below the floor a bump
# is premature (job-search practice runs slower than the sales cadences the
# research report imported — see docs/research/contact-strategy-findings.md);
# past the ceiling a "bump" of a weeks-old email reads as a re-send, and is
# surfaced as stale for a deliberate human call rather than offered.
BUMP_MIN_BUSINESS_DAYS = 5
BUMP_MAX_BUSINESS_DAYS = 15


def _business_days_since(sent_at: str) -> int:
    """Whole business days between a store timestamp ('YYYY-MM-DD ...',
    UTC) and today. Weekends don't count; a Friday send is bumpable the
    next Friday, not on Wednesday."""
    try:
        sent = datetime.strptime(sent_at[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return 0
    days, current = 0, sent
    while current < date.today():
        current += timedelta(days=1)
        if current.weekday() < 5:
            days += 1
    return days


def record_reply_findings(contacts: list[dict], states: dict, checked_at: str) -> None:
    """Persist what a live Gmail reply check found, for every definitive
    state. UNKNOWN is never recorded — "could not check" must stay
    distinguishable from silence. A REPLIED state whose Date header failed
    to parse is recorded at `checked_at` rather than dropped: the reply is
    the fact that arms the no-bump-after-reply gate; its exact timestamp
    is secondary. Shared by the replies command and the bump flow so the
    recording semantics cannot drift between the two write paths.
    """
    from outreach import replies as reply_check

    for row in contacts:
        state = states.get(row["company_slug"])
        if not state or state.state == reply_check.UNKNOWN:
            continue
        replied_at = None
        if state.state == reply_check.REPLIED:
            replied_at = state.replied_at or checked_at
        store.record_reply_check(
            row["company_slug"], replied_at=replied_at, checked_at=checked_at
        )


def _bump_status(row: dict, state, elapsed: int) -> tuple[str, str]:
    """Classify one contacted company for the follow-up. Pure — the caller
    owns recording. The branch order IS the policy: an answer (reply, then
    bounce) beats the one-bump cap, which beats the window; too_soon comes
    before unknown because rows below the floor are deliberately never
    checked against Gmail (no result there could change anything).
    """
    from outreach import replies as reply_check

    if row.get("replied_at") or (state and state.state == reply_check.REPLIED):
        return "replied", "they wrote back — answering is a human job, not a bump"
    if state and state.state == reply_check.BOUNCED:
        return "bounced", (f"bounce from {state.reply_from} — the address was "
                           f"wrong; a bump would bounce too")
    if row.get("follow_up_at"):
        return "bumped", f"follow-up already drafted {row['follow_up_at']}"
    if elapsed < BUMP_MIN_BUSINESS_DAYS:
        return "too_soon", (f"{elapsed} business day(s) since send; eligible "
                            f"at {BUMP_MIN_BUSINESS_DAYS}")
    if state is None or state.state == reply_check.UNKNOWN:
        return "unknown", ("could not confirm silence against Gmail; not "
                           "offering a bump on a guess")
    if elapsed > BUMP_MAX_BUSINESS_DAYS:
        return "stale", (f"{elapsed} business days out — past the bump window; "
                         f"only worth reopening with genuinely new information")
    return "eligible", f"silent for {elapsed} business days"


def bump_candidates() -> list[dict]:
    """Every contacted company, classified for the one permitted follow-up.

    Checks Gmail live for replies before offering anything — a bump
    crossing a reply in the mail is the failure this flow exists to
    prevent — and records what the check finds, so the data is kept even
    when no bump happens. Gmail is consulted only where the answer could
    matter: rows at or past the window floor without a recorded reply.
    Already-bumped rows stay in that set — a reply that arrives *after*
    the bump must still be seen, or "bumped" would read as silence
    forever. Rows below the floor are skipped (nothing a check finds
    could change "too_soon"); the `replies` command owns long-tail
    tracking.
    """
    from outreach import replies as reply_check

    contacted = [dict(r) for r in store.contacted()]
    if not contacted:
        return []

    elapsed_by_slug = {
        r["company_slug"]: _business_days_since(r["sent_at"]) for r in contacted
    }
    needs_check = [
        r for r in contacted
        if not r.get("replied_at")
        and elapsed_by_slug[r["company_slug"]] >= BUMP_MIN_BUSINESS_DAYS
    ]
    states = reply_check.check(needs_check) if needs_check else {}
    checked_at = reply_check.now_utc()
    record_reply_findings(needs_check, states, checked_at)

    out = []
    for row in contacted:
        slug = row["company_slug"]
        elapsed = elapsed_by_slug[slug]
        status, detail = _bump_status(row, states.get(slug), elapsed)
        claim = store.claim_row(slug)
        out.append({
            "company_slug": slug,
            "contact_email": row["contact_email"],
            "contact_name": claim["contact_name"] if claim else None,
            "contact_role": row.get("contact_role"),
            "job_title": claim["job_title"] if claim else None,
            "sent_at": row["sent_at"],
            "business_days": elapsed,
            "status": status,
            "detail": detail,
        })
    return out


def create_bump(company_slug: str, body: str) -> dict:
    """Put the one permitted follow-up into Gmail Drafts, in-thread.

    Every refusal fires BEFORE the draft is appended. The first version
    appended first and let `record_follow_up` refuse afterwards, which
    meant the refusal arrived with the forbidden duplicate already sitting
    in Gmail Drafts one click from sending — a guard reduced to
    bookkeeping. The checks, in order: the company was really sent to; its
    one bump is unspent; no reply is recorded; the window floor has
    passed; the body clears the same lint every other draft does; and
    Gmail confirms live, right now, that the thread is still silent — a
    morning `bumps` listing is not evidence about the afternoon. Only then
    is the draft appended and the bump recorded, with the store guard
    remaining as the backstop for a concurrent race.
    """
    from outreach import replies as reply_check

    state, log_row = store.claim_state(company_slug)
    if state != "sent":
        raise ValueError(
            f"{company_slug} is not a contacted company (state: {state}); "
            f"bumps only follow a real send"
        )
    row = dict(log_row)
    if row.get("follow_up_at"):
        raise store.AlreadyClaimed(
            f"{company_slug} already got its one follow-up on "
            f"{row['follow_up_at']} — there is no second bump"
        )
    if row.get("replied_at"):
        raise store.AlreadyClaimed(
            f"{company_slug} replied on {row['replied_at']} — a reply is "
            f"answered by a human, never bumped"
        )
    elapsed = _business_days_since(row["sent_at"])
    if elapsed < BUMP_MIN_BUSINESS_DAYS:
        raise ValueError(
            f"{company_slug} was sent to {elapsed} business day(s) ago; a "
            f"bump is premature before {BUMP_MIN_BUSINESS_DAYS}"
        )
    issues = lint(body)
    if issues:
        listed = "; ".join(str(i) for i in issues[:4])
        raise ValueError(
            f"bump body needs another pass ({len(issues)} issue(s)): {listed}"
        )

    states = reply_check.check([row])
    checked_at = reply_check.now_utc()
    record_reply_findings([row], states, checked_at)
    live = states.get(company_slug)
    if live is None or live.state == reply_check.UNKNOWN:
        raise RuntimeError(
            f"could not confirm against Gmail that {row['contact_email']} "
            f"stayed silent; not bumping on a guess"
        )
    if live.state == reply_check.REPLIED:
        raise store.AlreadyClaimed(
            f"{company_slug} replied ({live.replied_at or 'time unknown'}, "
            f"now recorded) — answer it instead of bumping"
        )
    if live.state == reply_check.BOUNCED:
        raise ValueError(
            f"the original email to {row['contact_email']} bounced — the "
            f"address was wrong, and a bump would bounce too"
        )

    subject = create_reply_draft(to=row["contact_email"], body=body)
    try:
        store.record_follow_up(company_slug)
    except store.AlreadyClaimed as exc:
        # A concurrent invocation won the race between our pre-flight and
        # this record. Surface it loudly instead of auto-trashing: the two
        # drafts are identical, and trash_draft matches by subject, so a
        # cleanup here could destroy the legitimate one too.
        raise store.AlreadyClaimed(
            f"{exc} — NOTE: this call had already appended a duplicate bump "
            f"draft ('{subject}' to {row['contact_email']}); delete one in "
            f"Gmail Drafts"
        ) from None
    return {
        "company_slug": company_slug,
        "contact_email": row["contact_email"],
        "subject": subject,
        "status": "bump_drafted",
    }


@dataclass
class FinalizeResult:
    company_slug: str
    email: str | None
    label: str
    drafted: bool
    message: str


def finalize(
    *,
    candidate: Candidate,
    contact_name: str,
    contact_role: str | None,
    domain: str,
    subject: str,
    body: str,
    observed_address: str | None = None,
    source_notes: str | None = None,
    contact_slate: str | None = None,
    linkedin: dict | None = None,
    ignore_prior_contact: bool = False,
    ignore_lint: bool = False,
) -> FinalizeResult:
    """Stages 4, 6 and 8, in the only order that is safe.

    Verify before drafting, because under the Gmail design a draft sits one
    click from sending. Claim only after the draft exists, so a failed
    append does not burn the company. The address is resolved from the
    domain's own pattern rather than from anything an agent inferred — see
    outreach/verify.py for why.
    """
    slug = candidate.company_slug
    if store.is_claimed(slug):
        state, existing = store.claim_state(slug)
        return FinalizeResult(slug, None, "skipped", False,
                              f"already {state} ({existing['contact_email']})")

    # Style is checked here rather than trusted to the prompt. Three
    # rounds of tightening the drafter's instructions did not stop it
    # drifting back to dense, mechanism-heavy prose, so the standard lives
    # in code and the caller retries against concrete issues.
    if not ignore_lint:
        issues = lint(body)
        if linkedin:
            issues += lint_linkedin(
                linkedin.get("connection_note"), linkedin.get("post_accept_dm"),
                linkedin.get("inmail_subject"), linkedin.get("inmail_body"),
            )
        if issues:
            listed = "; ".join(str(i) for i in issues[:4])
            return FinalizeResult(
                slug, None, "lint_failed", False,
                f"draft needs another pass ({len(issues)} issue(s)): {listed}",
            )

    # The store only knows what this pipeline did, and it started empty part
    # way through a job search that had been running by hand for months.
    # Gmail's sent folder is the real record. See outreach/history.py.
    if not ignore_prior_contact:
        prior = prior_contacts(
            company_slug=slug, domain=domain, contact_name=contact_name
        )
        if prior:
            lines = "; ".join(str(p) for p in prior[:3])
            return FinalizeResult(
                slug, None, "prior_contact", False,
                f"already emailed by hand: {lines}. Pass "
                f"ignore_prior_contact if this is a deliberate follow-up",
            )

    parts = contact_name.split()
    first, last = parts[0], (parts[-1] if len(parts) > 1 else "")

    email, verification = resolve_address(
        first, last, domain, fallback=observed_address
    )
    if email is None:
        return FinalizeResult(
            slug, None, verification.label, False,
            f"no deliverable address found ({verification.reason})",
        )

    create_draft(to=email, subject=subject, body=body, verification=verification)

    store.record_draft(
        company_slug=slug, platform=candidate.platform, job_id=candidate.job_id,
        job_title=candidate.job_title, job_url=candidate.job_url,
        score=candidate.score, contact_name=contact_name,
        contact_role=contact_role, contact_email=email,
        confidence=verification.label, draft_subject=subject,
        source_notes=source_notes, contact_slate=contact_slate,
        linkedin_json=json.dumps(linkedin) if linkedin else None,
    )
    return FinalizeResult(
        slug, email, verification.label, True,
        f"drafted to {email} ({verification.label}, score {verification.score}), "
        f"résumé {resume_path().name}",
    )
