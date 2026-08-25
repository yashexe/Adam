#!/usr/bin/env python3
"""
Outreach pipeline CLI — the deterministic half.

    python3 outreach_run.py prepare --days 1        # who is worth a contact call
    python3 outreach_run.py prepare --days 1 --json # same, for the skill to consume
    python3 outreach_run.py status                  # pending drafts and contacted companies
    python3 outreach_run.py discard <company>        # draft got deleted in Gmail, release the claim

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
from outreach.pipeline import DEFAULT_MIN_SCORE, Candidate, finalize, prepare
from qualify.semantic import build_batch, save_scores, unjudged


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
        print(f"{'SCORE':>5}  {'CONF':>4}  {'COMPANY':<20}  TITLE")
        print("-" * 92)
        for c in candidates:
            conf = f"{c.confidence}%" if c.confidence is not None else "-"
            print(f"{c.score:>5}  {conf:>4}  {c.company_slug[:20]:<20}  {c.job_title[:44]}")
            if c.funding_hint:
                print(f"{'':>13}  {'':<20}  funding: {c.funding_hint}")
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
    print(f"# {len(pending)} posting(s) to judge\n", file=sys.stderr)
    print(build_batch(pending))
    return 0


def cmd_judge_save(args: argparse.Namespace) -> int:
    """Persist the judge's JSON array, read from stdin."""
    scores = json.load(sys.stdin)
    total = save_scores(scores)
    print(f"saved {len(scores)} judgement(s); {total} cached in total")
    return 0


def _judge_candidates(*, days: int, limit: int | None) -> list[dict]:
    """Eligible postings in the window, with the description text the judge
    needs. Scored deterministically first so nothing ineligible is judged."""
    from qualify.boards import job_data_for
    from qualify.candidates import fetch_candidates
    from qualify.eligibility import check_title

    out = []
    for row in fetch_candidates(days=days, limit=limit):
        if not check_title(row.get("title") or "")[0]:
            continue
        job_data = job_data_for(row)
        if job_data is None:
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


def cmd_finalize(args: argparse.Namespace) -> int:
    """Read one JSON payload on stdin: the candidate, the contact Agent 1
    found, and the draft Agent 2 wrote. Verifies, drafts into Gmail, claims
    the company. Taking JSON on stdin rather than a wall of flags keeps the
    skill's invocation readable and avoids shell-quoting a multi-paragraph
    email body."""
    payload = json.load(sys.stdin)
    candidate = Candidate(**payload["candidate"])
    result = finalize(
        candidate=candidate,
        contact_name=payload["contact_name"],
        contact_role=payload.get("contact_role"),
        domain=payload["domain"],
        subject=payload["subject"],
        body=payload["body"],
        observed_address=payload.get("observed_address"),
        source_notes=payload.get("source_notes"),
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
    for row in contacted:
        state = states.get(row["company_slug"])
        if not state or state.state == reply_check.UNKNOWN:
            continue  # never record a guess as a finding
        store.record_reply_check(
            row["company_slug"],
            replied_at=state.replied_at if state.state == reply_check.REPLIED else None,
            checked_at=checked_at,
        )

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

    f = sub.add_parser("finalize", help="verify + draft into Gmail + claim (JSON on stdin)")
    f.set_defaults(func=cmd_finalize)

    s = sub.add_parser("status", help="show pending drafts and contacted companies")
    s.set_defaults(func=cmd_status)

    x = sub.add_parser("discard", help="trash the Gmail draft and release the claim")
    x.add_argument("company", help="company_slug to discard")
    x.set_defaults(func=cmd_discard)

    r = sub.add_parser("replies", help="check Gmail for replies and record them")
    r.set_defaults(func=cmd_replies)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
