#!/usr/bin/env python3
"""
Outreach pipeline CLI — the deterministic half.

    python3 outreach_run.py prepare --days 1        # who is worth a contact call
    python3 outreach_run.py prepare --days 1 --json # same, for the skill to consume
    python3 outreach_run.py verify-slate            # which slate candidates are reachable (JSON stdin)
    python3 outreach_run.py status                  # pending drafts and contacted companies
    python3 outreach_run.py bumps                   # who is eligible for the one follow-up
    python3 outreach_run.py bump <company>           # draft that follow-up (body on stdin)
    python3 outreach_run.py discard <company>        # draft got deleted in Gmail, release the claim
    python3 outreach_run.py verifiers                # which verification providers can answer right now
    python3 outreach_run.py ignore <slug> "<reason>" # this slug is not a company (marketplace, agency)
    python3 outreach_run.py slates                   # researched companies awaiting a pick
    python3 outreach_run.py slate approve <slug> "<name>" | dismiss <slug> ["<reason>"]
    python3 outreach_run.py slate-save                  # park a researched slate (JSON on stdin)
    python3 outreach_run.py slate-candidate <slug>      # everything needed to draft a parked slate
    python3 outreach_run.py tick [--since "YYYY-MM-DD HH:MM:SS"]   # the 5-minute check: prints fire|idle
    python3 outreach_run.py budget                   # what an unattended run may spend right now
    python3 outreach_run.py unattended-start <slug>  # the run is about to research this company
    python3 outreach_run.py run-done --status ok|fail|timeout [--summary-file F]
    python3 outreach_run.py verify <email>           # one address through the chain (smoke-test a new key)

Stages 3 and 5 are agent calls and are not driven from here — the
`outreach` skill runs them between `prepare` and `finalize`. Nothing in
this file sends email.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from outreach import store
from outreach.pipeline import (
    DEFAULT_MIN_SCORE,
    Candidate,
    bump_candidates,
    create_bump,
    finalize,
    prepare,
    record_reply_findings,
    resolve_candidate_slate,
)
from outreach.verify import provider_status, verify_email
from qualify.semantic import build_batch, cached_score, save_scores, unjudged

# Cap on postings per judge invocation — see cmd_judge.
JUDGE_BATCH_CAP = 40


def cmd_prepare(args: argparse.Namespace) -> int:
    try:
        candidates, skipped = prepare(
            days=args.days, limit=args.limit, min_score=args.min_score
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([dataclasses.asdict(c) for c in candidates], indent=2))
        return 0

    print(f"\n{len(candidates)} company(s) worth a contact call "
          f"({args.days}d window, score >= {args.min_score})\n")
    if candidates:
        print(f"{'SCORE':>5}  {'COMPANY':<20}  TITLE")
        print("-" * 92)
        for c in candidates:
            print(f"{c.score:>5}  {c.company_slug[:20]:<20}  {c.job_title[:56]}")
            if c.judge_reason:
                print(f"{'':>5}  {'':<20}  {c.judge_reason[:64]}")
            if c.funding_hint:
                print(f"{'':>5}  {'':<20}  funding: {c.funding_hint}")
    else:
        print("(nothing new above the bar)")

    if skipped:
        print(f"\nskipped by dedup ({len(skipped)}):")
        for line in skipped:
            print(f"  {line}")
    return 0


def cmd_judge(args: argparse.Namespace) -> int:
    """Emit the batch of unjudged postings for the relevance-judge agent.

    Prints nothing when everything in the window is already judged, which
    makes re-running the pipeline free: judgements are cached per posting,
    not per run.
    """
    rows = _judge_candidates(days=args.days, limit=args.limit)
    pending = unjudged(rows)
    if not pending:
        print(f"all {len(rows)} posting(s) in the window are already judged",
              file=sys.stderr)
        return 0
    # One agent call judges ~40 postings reliably; a 218-posting single
    # batch has failed outright. With Lever/Workable in the tracker the
    # window can easily exceed the cap, so emit at most one batch per
    # invocation and say how many remain — the caller loops judge/
    # judge-save until this prints "already judged".
    batch = pending[:JUDGE_BATCH_CAP]
    note = f"# {len(batch)} posting(s) to judge"
    if len(pending) > len(batch):
        note += (f" ({len(pending) - len(batch)} more unjudged in the window; "
                 f"run judge again after judge-save)")
    print(note + "\n", file=sys.stderr)
    print(build_batch(batch))
    return 0


def cmd_judge_save(args: argparse.Namespace) -> int:
    """Persist the judge's JSON array, read from stdin. Anchor entries are
    validated against their expected bands and never cached; a warning
    here means the judge has drifted and the batch deserves suspicion."""
    scores = json.load(sys.stdin)
    total, warnings = save_scores(scores)
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    print(f"saved {len(scores)} judgement(s); {total} cached in total")
    return 0


def _judge_candidates(*, days: int, limit: int | None) -> list[dict]:
    """Eligible postings in the window, with the description text the judge
    needs. Scored deterministically first so nothing ineligible is judged."""
    from qualify.boards import job_data_for
    from qualify.candidates import fetch_candidates
    from qualify.eligibility import check_citizenship_required, check_title, check_years
    from qualify.extractor import heuristic_extract_requirements

    out = []
    for row in fetch_candidates(days=days, limit=limit):
        if not check_title(row.get("title") or "")[0]:
            continue
        job_data = job_data_for(row)
        if job_data is None:
            continue
        reqs = heuristic_extract_requirements(job_data["description_text"])
        if not check_years(reqs.get("years_experience_min"))[0]:
            continue
        if not check_citizenship_required(reqs.get("citizenship_required"))[0]:
            continue
        out.append({
            "platform": row["platform"],
            "job_id": str(row["job_id"]),
            "company_slug": row["company_slug"],
            "job_title": job_data["title"],
            "department": job_data.get("department"),
            "description_text": job_data["description_text"],
        })
    return out


def cmd_verify_slate(args: argparse.Namespace) -> int:
    """Read {"domain": ..., "candidates": [{"name", "role", ...}, ...]} on
    stdin — Agent 1's ranked slate — and print each candidate with the
    address that would be used and whether it is reachable. One cached
    domain-search for the whole slate; a verification credit is spent only
    until the first deliverable candidate. Advisory: finalize re-verifies
    whichever candidate the human actually picks."""
    payload = json.load(sys.stdin)
    candidates = payload.get("candidates") or []
    if not candidates:
        print("error: no candidates in payload", file=sys.stderr)
        return 1
    domain = payload.get("domain")
    if not domain:
        print("error: no domain in payload — Agent 1 could not establish the "
              "company's email domain, so there is nothing to resolve "
              "against; skip the company or re-run the contact search",
              file=sys.stderr)
        return 1
    resolved = resolve_candidate_slate(
        domain, candidates, observed_address=payload.get("observed_address")
    )
    print(json.dumps(resolved, indent=2))
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    """Read one JSON payload on stdin: the candidate, the contact chosen
    from Agent 1's slate, and the draft Agent 2 wrote. Verifies, drafts
    into Gmail, claims the company. Taking JSON on stdin rather than a wall
    of flags keeps the skill's invocation readable and avoids shell-quoting
    a multi-paragraph email body."""
    payload = json.load(sys.stdin)
    candidate = Candidate(**payload["candidate"])
    slate = payload.get("contact_slate")
    result = finalize(
        candidate=candidate,
        contact_name=payload["contact_name"],
        contact_role=payload.get("contact_role"),
        domain=payload["domain"],
        subject=payload["subject"],
        body=payload["body"],
        observed_address=payload.get("observed_address"),
        source_notes=payload.get("source_notes"),
        contact_slate=json.dumps(slate) if slate is not None else None,
        linkedin=payload.get("linkedin"),
        ignore_prior_contact=payload.get("ignore_prior_contact", False),
        ignore_lint=payload.get("ignore_lint", False),
    )
    print(json.dumps(dataclasses.asdict(result), indent=2))
    return 0 if result.drafted else 2


def cmd_status(args: argparse.Namespace) -> int:
    pending, contacted, discarded = store.pending(), store.contacted(), store.discarded()
    print(f"\npending drafts ({len(pending)}) — sitting in Gmail, unsent\n")
    for r in pending:
        print(f"  {r['company_slug']:<18} score {str(r['score']):<4} "
              f"{r['contact_email']:<28} {r['confidence']:<10} {r['job_title']}")
        print(f"  {'':<18} {r['contact_name']} ({r['contact_role']})")
        if r["superseded_note"]:
            print(f"  {'':<18} note: {r['superseded_note']}")
    print(f"\ncontacted ({len(contacted)}) — closed, will never be drafted again\n")
    for r in contacted:
        print(f"  {r['company_slug']:<18} {r['contact_email']:<28} "
              f"{r['outcome']:<8} {r['sent_at']}")
    if not contacted:
        print("  (none yet)")
    if discarded:
        print(f"\ndiscarded ({len(discarded)}) — draft deleted, open for a future attempt\n")
        for r in discarded:
            print(f"  {r['company_slug']:<18} was {r['contact_email']:<28} {r['job_title']}")
    print()
    return 0


def cmd_replies(args: argparse.Namespace) -> int:
    """Check Gmail for replies to everything already contacted, and record
    what it finds. Read-only against Gmail; writes only to outreach.db."""
    from outreach import replies as reply_check

    contacted = [dict(r) for r in store.contacted()]
    if not contacted:
        print("nothing contacted yet, so nothing to check")
        return 0

    states = reply_check.check(contacted)
    checked_at = reply_check.now_utc()
    # Recording (including the never-record-a-guess rule) is shared with
    # the bump flow so the two write paths cannot drift.
    record_reply_findings(contacted, states, checked_at)

    print(f"\nchecked {len(contacted)} contacted company(s)\n")
    for row in contacted:
        state = states.get(row["company_slug"])
        label = state.state if state else reply_check.UNKNOWN
        line = f"  {row['company_slug']:<18} {row['contact_email']:<30} {label}"
        if state and state.replied_at:
            line += f"  {state.replied_at}"
        print(line)
        if state and state.state == reply_check.BOUNCED:
            print(f"  {'':<18} bounce from {state.reply_from} — the address was wrong")

    rates = store.reply_rates()
    if rates:
        print(f"\n{'ROLE':<34} {'SENT':>5} {'REPLIED':>8} {'UNCHECKED':>10}")
        print("-" * 60)
        for r in rates:
            print(f"  {str(r['role'])[:32]:<32} {r['sent']:>5} "
                  f"{r['replied']:>8} {r['unchecked']:>10}")
        total = sum(r["sent"] for r in rates)
        if total < 10:
            print(f"\n{total} sent in total — far too few to conclude anything "
                  f"about which contacts respond. The point is that it is now "
                  f"being recorded.")
    print()
    return 0


def cmd_bumps(args: argparse.Namespace) -> int:
    """Classify every contacted company for the one permitted follow-up.

    Checks Gmail live for replies first (and records what it finds), so an
    'eligible' here means confirmed silence, not assumed silence."""
    rows = bump_candidates()
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("nothing contacted yet, so nothing to bump")
        return 0
    print(f"\n{len(rows)} contacted company(s)\n")
    for r in rows:
        print(f"  {r['company_slug']:<18} {r['contact_email']:<30} "
              f"{r['status']:<10} {r['detail']}")
        if r.get("contact_name") or r.get("job_title"):
            who = " — ".join(
                part for part in (r.get("contact_name"), r.get("job_title")) if part
            )
            print(f"  {'':<18} {who}")
    eligible = [r for r in rows if r["status"] == "eligible"]
    if eligible:
        print(f"\n{len(eligible)} eligible for their one follow-up bump — "
              f"drafting one is a human decision, made per company")
    print()
    return 0


def cmd_bump(args: argparse.Namespace) -> int:
    """Create the one permitted follow-up draft for a company. The bump
    body (Agent 2's, human-reviewed intent) arrives on stdin; the draft
    lands as a reply in the original Gmail thread, with no résumé attached.
    The store refuses a second bump and refuses to bump a reply."""
    body = sys.stdin.read().strip()
    if not body:
        print("error: bump body expected on stdin", file=sys.stderr)
        return 1
    try:
        result = create_bump(args.company, body)
    except (ValueError, store.AlreadyClaimed, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def cmd_discard(args: argparse.Namespace) -> int:
    """Trash the matching Gmail draft (if it still exists) and release the
    claim, so this company can be drafted to again. A company that was
    actually sent to cannot be discarded — that record is permanent."""
    from outreach.gmail_draft import trash_draft

    state, row = store.claim_state(args.company)
    if state is None:
        print(f"error: {args.company} has no pending draft", file=sys.stderr)
        return 1
    if state == "sent":
        print(f"error: {args.company} was already contacted at "
              f"{row['contact_email']} on {row['sent_at']} — cannot discard",
              file=sys.stderr)
        return 1
    if state == store.DISCARDED:
        print(f"{args.company} was already discarded")
        return 0

    moved = trash_draft(to=row["contact_email"], subject=row["draft_subject"])
    store.discard_draft(args.company)
    print(json.dumps({
        "company_slug": args.company,
        "contact_email": row["contact_email"],
        "gmail_drafts_trashed": moved,
        "status": "discarded",
    }, indent=2))
    return 0


def cmd_verifiers(args: argparse.Namespace) -> int:
    """Which providers in the verification chain can answer right now, and
    what each has left. Run it before a drain and right after adding a
    key — a chain where nothing is ready lands every draft `unverified`."""
    rows = provider_status()
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print("\nverification providers, in chain order\n")
    for r in rows:
        print(f"  {'ready' if r['ready'] else '  off'}  {r['provider']:<16} {r['detail']}")
    print()
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """One address through the provider chain. The smoke test for a newly
    added key, and the way to see exactly why an address came back
    `unverified`. Spends a credit only if the SMTP probe cannot answer."""
    result = verify_email(args.email, use_cache=not args.fresh)
    print(json.dumps({
        "email": result.email,
        "label": result.label,
        "score": result.score,
        "reason": result.reason,
        "blocks_draft": result.should_block,
    }, indent=2))
    return 2 if result.should_block else 0


def cmd_ignore(args: argparse.Namespace) -> int:
    """Record that a slug is not a company to email. A human decision with a
    reason, not a fit filter: fit stays the judge's, and stays broad."""
    if args.remove:
        store.unignore_company(args.slug)
        print(f"{args.slug}: no longer ignored")
        return 0
    if not args.reason:
        print("error: a reason is required (it is shown every time prepare skips the slug)",
              file=sys.stderr)
        return 1
    store.ignore_company(args.slug, args.reason)
    print(f"{args.slug}: ignored — {args.reason}")
    return 0


def cmd_slates(args: argparse.Namespace) -> int:
    """Researched companies parked for a human pick, plus their history."""
    rows = [dict(r) for r in store.slates(args.status)]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no slates" + (f" with status {args.status}" if args.status else ""))
        return 0
    print(f"\n{'STATUS':<10} {'SCORE':>5}  {'COMPANY':<18}  TITLE / CANDIDATES")
    print("-" * 92)
    for r in rows:
        print(f"{r['status']:<10} {str(r['score']):>5}  {r['company_slug'][:18]:<18}  {(r['job_title'] or '')[:50]}")
        try:
            resolved = json.loads(r["resolved_json"] or "[]")
        except json.JSONDecodeError:
            resolved = []
        for c in resolved:
            mark = "→ " if c.get("name") == r["chosen_name"] else "  "
            label = c.get("verify_label") or "unresolved"
            addr = c.get("address") or "-"
            print(f"{'':<10} {'':>5}  {'':<18}  {mark}{c.get('name','?')} ({c.get('role','?')}) — {addr} [{label}]")
        if r["reason"]:
            print(f"{'':<10} {'':>5}  {'':<18}  note: {r['reason']}")
    print()
    return 0


def cmd_slate(args: argparse.Namespace) -> int:
    """approve <slug> <name>: the human picked; the next run drafts it.
    dismiss <slug> [reason]: not this posting; a better one re-opens it."""
    try:
        if args.action == "approve":
            if not args.name:
                print("error: approve needs the chosen candidate's name", file=sys.stderr)
                return 1
            row = store.approve_slate(args.slug, args.name)
        else:
            row = store.dismiss_slate(args.slug, args.name)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"company_slug": row["company_slug"], "status": row["status"],
                      "chosen_name": row["chosen_name"], "reason": row["reason"]}, indent=2))
    return 0


def cmd_slate_save(args: argparse.Namespace) -> int:
    """Park a researched, resolved slate for a human pick. JSON on stdin:
    {"candidate": {...prepare's object...}, "domain": ..., "slate": [...],
     "resolved": [...verify-slate output...], "observed_address": ...,
     "source_notes": ..., "personalization_context": [...],
     "status": "awaiting", "reason": "why no draft"}."""
    payload = json.load(sys.stdin)
    cand = payload["candidate"]
    store.save_slate(
        company_slug=cand["company_slug"], platform=cand["platform"], job_id=str(cand["job_id"]),
        job_title=cand.get("job_title"), job_url=cand.get("job_url"), score=cand.get("score"),
        domain=payload.get("domain"), slate_json=json.dumps(payload.get("slate") or []),
        resolved_json=json.dumps(payload.get("resolved") or []),
        observed_address=payload.get("observed_address"),
        source_notes=payload.get("source_notes"),
        personalization_json=json.dumps(payload.get("personalization_context") or []),
        status=payload.get("status") or store.SLATE_AWAITING, reason=payload.get("reason"),
        description_text=cand.get("description_text"),
    )
    print(json.dumps({"company_slug": cand["company_slug"], "status": payload.get("status") or "awaiting"}))
    return 0


def cmd_slate_candidate(args: argparse.Namespace) -> int:
    """Rebuild everything the drafter and finalize need for a parked slate,
    so an approved pick can be drafted without re-researching. The posting
    text comes from the board cache rather than being stored twice."""
    from qualify.boards import job_data_for
    row = store.slate_row(args.slug)
    if row is None:
        print(f"error: no slate for {args.slug}", file=sys.stderr)
        return 1
    stored = row["description_text"] if "description_text" in row.keys() else None
    if stored:
        job_data = {"title": row["job_title"], "description_text": stored}
    else:
        job_data = job_data_for({"platform": row["platform"], "job_id": row["job_id"],
                                 "company_slug": row["company_slug"], "title": row["job_title"]})
    if job_data is None:
        print(f"error: the posting behind {args.slug}'s slate is gone from its board and "
              f"no text was stored", file=sys.stderr)
        return 1
    judged = cached_score(row["platform"], row["job_id"]) or {}
    out = {
        "candidate": {
            "company_slug": row["company_slug"], "platform": row["platform"],
            "job_id": row["job_id"], "job_title": job_data["title"], "job_url": row["job_url"],
            "score": row["score"], "judge_reason": judged.get("reason"), "funding_hint": None,
            "description_text": job_data["description_text"],
        },
        "domain": row["domain"], "status": row["status"], "chosen_name": row["chosen_name"],
        "slate": json.loads(row["slate_json"] or "[]"),
        "resolved": json.loads(row["resolved_json"] or "[]"),
        "observed_address": row["observed_address"], "source_notes": row["source_notes"],
        "personalization_context": json.loads(row["personalization_json"] or "[]"),
        "reason": row["reason"],
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_tick(args: argparse.Namespace) -> int:
    from outreach import unattended
    verdict, reason = unattended.tick(since=args.since)
    print(verdict)
    print(reason, file=sys.stderr)
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    from outreach import unattended
    print(json.dumps(unattended.budget(), indent=2))
    return 0


def cmd_unattended_start(args: argparse.Namespace) -> int:
    from outreach import unattended
    print(json.dumps(unattended.start_company(args.slug), indent=2))
    return 0


def cmd_run_done(args: argparse.Namespace) -> int:
    from outreach import unattended
    summary = None
    if args.summary_file:
        try:
            summary = open(args.summary_file).read()
        except OSError:
            summary = None
    print(json.dumps(unattended.run_done(None, args.status, summary), indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="score recent matches and apply dedup")
    p.add_argument("--days", type=int, default=1)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--min-score", type=int, default=DEFAULT_MIN_SCORE)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_prepare)

    j = sub.add_parser("judge", help="emit unjudged postings for the relevance-judge")
    j.add_argument("--days", type=int, default=1)
    j.add_argument("--limit", type=int, default=None)
    j.set_defaults(func=cmd_judge)

    js = sub.add_parser("judge-save", help="persist the judge's JSON array (stdin)")
    js.set_defaults(func=cmd_judge_save)

    vs = sub.add_parser("verify-slate",
                        help="resolve reachability for Agent 1's slate (JSON on stdin)")
    vs.set_defaults(func=cmd_verify_slate)

    f = sub.add_parser("finalize", help="verify + draft into Gmail + claim (JSON on stdin)")
    f.set_defaults(func=cmd_finalize)

    b = sub.add_parser("bumps", help="classify contacted companies for the one follow-up")
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_bumps)

    bp = sub.add_parser("bump", help="draft the one follow-up for a company (body on stdin)")
    bp.add_argument("company", help="company_slug to bump")
    bp.set_defaults(func=cmd_bump)

    s = sub.add_parser("status", help="show pending drafts and contacted companies")
    s.set_defaults(func=cmd_status)

    x = sub.add_parser("discard", help="trash the Gmail draft and release the claim")
    x.add_argument("company", help="company_slug to discard")
    x.set_defaults(func=cmd_discard)

    r = sub.add_parser("replies", help="check Gmail for replies and record them")
    r.set_defaults(func=cmd_replies)

    v = sub.add_parser("verifiers", help="which verification providers can answer right now")
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_verifiers)

    ve = sub.add_parser("verify", help="run one address through the verification chain")
    ve.add_argument("email")
    ve.add_argument("--fresh", action="store_true", help="ignore a cached answer")
    ve.set_defaults(func=cmd_verify)

    ig = sub.add_parser("ignore", help="mark a slug as not-a-company (marketplace, agency)")
    ig.add_argument("slug")
    ig.add_argument("reason", nargs="?", default="")
    ig.add_argument("--remove", action="store_true", help="stop ignoring the slug")
    ig.set_defaults(func=cmd_ignore)

    sl = sub.add_parser("slates", help="researched companies awaiting a human pick")
    sl.add_argument("--status", choices=["awaiting", "approved", "drafted", "dismissed"])
    sl.add_argument("--json", action="store_true")
    sl.set_defaults(func=cmd_slates)

    sa = sub.add_parser("slate", help="approve <slug> <name> | dismiss <slug> [reason]")
    sa.add_argument("action", choices=["approve", "dismiss"])
    sa.add_argument("slug")
    sa.add_argument("name", nargs="?", help="chosen candidate (approve) or reason (dismiss)")
    sa.set_defaults(func=cmd_slate)

    ss = sub.add_parser("slate-save", help="park a researched slate for a human pick (JSON on stdin)")
    ss.set_defaults(func=cmd_slate_save)

    sc = sub.add_parser("slate-candidate", help="everything needed to draft a parked slate")
    sc.add_argument("slug")
    sc.set_defaults(func=cmd_slate_candidate)

    tk = sub.add_parser("tick", help="the 5-minute unattended check; prints fire or idle")
    tk.add_argument("--since", help="reset the watermark (tracker format, UTC) to back-fill")
    tk.set_defaults(func=cmd_tick)

    bg = sub.add_parser("budget", help="what an unattended run may spend right now")
    bg.set_defaults(func=cmd_budget)

    us = sub.add_parser("unattended-start", help="count a company against today's budget")
    us.add_argument("slug")
    us.set_defaults(func=cmd_unattended_start)

    rd = sub.add_parser("run-done", help="the wrapper reports how an unattended run ended")
    rd.add_argument("--status", required=True, choices=["ok", "fail", "timeout", "stale"])
    rd.add_argument("--summary-file")
    rd.set_defaults(func=cmd_run_done)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
