#!/usr/bin/env python3
"""
QUALIFY — stage 2 of the pipeline, standalone.

Scores recent tracker matches against the profile and prints the ranking.
Reads the live tracker DB read-only, writes nothing anywhere, and calls no
LLM: every number below is deterministic and reproducible.

    python3 qualify_run.py --days 7 --limit 25
    python3 qualify_run.py --days 3 --detail
    python3 qualify_run.py --days 7 --min-score 65 --json

Note on the score itself: semantic_fit (weight 30) is deliberately left
unscored — `score_job` treats an absent embedding as an unknown dimension,
dropping it from the denominator instead of zeroing it, so the open
sentence-transformers-vs-LLM decision stays open without distorting
anything. `confidence` reports how much dimension weight actually had data.
"""

from __future__ import annotations

import argparse
import json
import sys

from qualify.boards import job_data_for
from qualify.candidates import fetch_candidates
from qualify.eligibility import check_title
from qualify.extractor import heuristic_extract_requirements
from qualify.profile import PREFERENCES, PROFILE
from qualify.scorer import score_job
from qualify.semantic import cached_score, cached_similarity

# From docs/qualify.md. Inherited from Instaply, where they gated "email the
# user" rather than "spend an agentic contact-search call" — a different
# cost/benefit, so treat them as a starting point to calibrate against real
# output, not as settled thresholds.
# Re-tuned 2026-08-24 against the 56-posting judged sample (docs/qualify.md).
# Instaply's inherited 85 sat above the entire observed distribution (max
# composite: 83) because the semantic dimension maps the judge's 0-100 onto
# the scorer's similarity band. The judge-confirmed strong cluster bottoms
# out at 73 with an empty gap down to 69; 72 splits that gap with a point
# of slack for jitter.
TIERS = ((72, "strong"), (65, "worth a look"), (0, "below bar"))


def tier_for(score: int) -> str:
    return next(label for floor, label in TIERS if score >= floor)


def score_row(row: dict, *, refresh: bool = False) -> dict | None:
    """Score one posting. None when it is ineligible or has left its board."""
    if not check_title(row.get("title") or "")[0]:
        return None
    job_data = job_data_for(row, refresh=refresh)
    if job_data is None:
        return None

    extracted_reqs = heuristic_extract_requirements(job_data["description_text"])
    judged = cached_score(row["platform"], row["job_id"])
    total, breakdown = score_job(
        job_data=job_data,
        preferences=PREFERENCES,
        profile=PROFILE,
        extracted_reqs=extracted_reqs,
        semantic_similarity=cached_similarity(row["platform"], row["job_id"]),
        # No hard filters run in this MVP; an empty result set means no
        # "uncertain" verdicts, so confidence is not discounted.
        filter_results={"results": {}},
    )
    return {
        "score": total,
        "tier": tier_for(total),
        "confidence": breakdown.pop("confidence", None),
        "company": row["company_slug"],
        "platform": row["platform"],
        "title": job_data["title"],
        "url": row.get("url"),
        "first_seen_at": row.get("first_seen_at"),
        "funding_hint": row.get("funding_hint"),
        "breakdown": breakdown,
        "required_skills": extracted_reqs.get("required_skills") or [],
        "years_required": extracted_reqs.get("years_experience_min"),
        "role_family": extracted_reqs.get("role_family"),
        "semantic": judged,
    }


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def print_table(results: list[dict], *, detail: bool) -> None:
    print(f"{'SCORE':>5}  {'CONF':>4}  {'TIER':<12}  {'COMPANY':<22}  TITLE")
    print("-" * 100)
    for r in results:
        print(
            f"{r['score']:>5}  {str(r['confidence']) + '%':>4}  {r['tier']:<12}  "
            f"{_truncate(r['company'], 22):<22}  {_truncate(r['title'], 48)}"
        )
        if detail:
            dims = "  ".join(f"{k}={v}" for k, v in sorted(r["breakdown"].items()))
            print(f"         {dims}")
            print(
                f"         role_family={r['role_family']}  "
                f"years_required={r['years_required']}  "
                f"skills_in_posting={len(r['required_skills'])}"
            )
            if r["funding_hint"]:
                print(f"         funding: {r['funding_hint']}")
            print(f"         {r['url']}")
            print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7,
                        help="how far back in seen_jobs to look (default 7)")
    parser.add_argument("--limit", type=int, default=25,
                        help="max postings to score (default 25)")
    parser.add_argument("--min-score", type=int, default=0,
                        help="only show results at or above this score")
    parser.add_argument("--detail", action="store_true",
                        help="print the per-dimension breakdown")
    parser.add_argument("--refresh", action="store_true",
                        help="bypass the cached board responses")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of a table")
    args = parser.parse_args()

    try:
        rows = fetch_candidates(days=args.days, limit=args.limit)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    results, missing = [], 0
    for row in rows:
        scored = score_row(row, refresh=args.refresh)
        if scored is None:
            missing += 1
            continue
        results.append(scored)

    results.sort(key=lambda r: r["score"], reverse=True)
    shown = [r for r in results if r["score"] >= args.min_score]

    if args.json:
        print(json.dumps(shown, indent=2))
        return 0

    print(
        f"\nQUALIFY — {len(results)} postings scored "
        f"({args.days}d window, cap {args.limit})\n"
    )
    if shown:
        print_table(shown, detail=args.detail)
    else:
        print("(nothing at or above the requested score)")

    counts = {label: 0 for _, label in TIERS}
    for r in results:
        counts[r["tier"]] += 1
    print(
        "\n" + " · ".join(f"{label}: {counts[label]}" for _, label in TIERS)
    )
    if missing:
        print(f"{missing} posting(s) no longer on their board — skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
