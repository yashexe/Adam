"""
The deterministic spine of the pipeline.

Stages 1, 2, 4, 6 and 8 are all here. Stages 3 and 5 are not, and cannot
be: they are agent calls, driven by the `outreach` skill from a Claude Code
session. This module is what that session calls before and after each of
them, so every step with a lasting consequence — what gets contacted,
whether an address is real, what lands in Gmail, what is recorded — runs as
ordinary code that can be read and tested, not as a model's decision.

    prepare()   stages 1-2 + dedup   -> companies worth an Agent 1 call
      [ agent 1: find the contact ]
      [ agent 2: write the draft  ]
    finalize()  stages 4, 6, 8      -> verify, draft into Gmail, claim
"""

from __future__ import annotations

from dataclasses import dataclass, field

from qualify.boards import job_data_for
from qualify.candidates import fetch_candidates
from qualify.eligibility import check_citizenship_required, check_title, check_years
from qualify.extractor import heuristic_extract_requirements
from qualify.semantic import cached_score

from outreach import store
from outreach.draft_lint import lint
from outreach.gmail_draft import create_draft
from outreach.history import prior_contacts
from outreach.verify import resolve_address, verify_email

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
        source_notes=source_notes,
    )
    return FinalizeResult(
        slug, email, verification.label, True,
        f"drafted to {email} ({verification.label}, score {verification.score})",
    )
