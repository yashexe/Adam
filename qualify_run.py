#!/usr/bin/env python3
"""
QUALIFY — stage 2 of the pipeline, standalone.

Ranks recent tracker matches by the cached judge score and prints the
result. Reads the live tracker DB read-only, writes nothing anywhere, and
calls no LLM: judgements come from `.cache/semantic.json`, where the
`outreach` skill's judge step put them.

    python3 qualify_run.py --days 7 --limit 25
    python3 qualify_run.py --days 3 --detail
    python3 qualify_run.py --days 7 --min-score 65 --json

The judge's 0-100 IS the score since 2026-08-26 — there is no composite
around it (docs/qualify.md, "The judge becomes the score"). A posting
that has not been judged has no score at all: it is listed separately as
unjudged, never ranked on partial information. Deterministic extraction
still runs, but only to apply the hard eligibility rules and to show
metadata next to a score.
"""

from __future__ import annotations

import argparse
import json
import sys

from qualify.boards import job_data_for
from qualify.candidates import fetch_candidates
from qualify.eligibility import check_citizenship_required, check_title, check_years
from qualify.extractor import heuristic_extract_requirements
from qualify.semantic import cached_score

# On the judge's own scale, derived from the re-judged 303-posting corpus
# and the interview ground truth rather than inherited (docs/qualify.md,
# "The judge becomes the score"): the strong cluster is dense from 72 up
# with 69-71 completely empty, so 70 splits that gap; 65 is the judge
# scale's own boundary between "real fit with some distance" and
# "adjacent", and both ground-truth rejections sat below it (55, 58).
# Keep the "worth a look" floor consistent with DEFAULT_MIN_SCORE in
# outreach/pipeline.py.
TIERS = ((70, "strong"), (65, "worth a look"), (0, "below bar"))


def tier_for(score: int) -> str:
    return next(label for floor, label in TIERS if score >= floor)


def score_row(row: dict, *, refresh: bool = False) -> dict | str | None:
    """One posting's ranked entry. None when it is ineligible or has left
    its board; the string "unjudged" when it is eligible but has no cached
    judgement to rank on."""
    if not check_title(row.get("title") or "")[0]:
        return None
    job_data = job_data_for(row, refresh=refresh)
    if job_data is None:
        return None

    extracted_reqs = heuristic_extract_requirements(job_data["description_text"])
    if not check_years(extracted_reqs.get("years_experience_min"))[0]:
        return None
    if not check_citizenship_required(extracted_reqs.get("citizenship_required"))[0]:
        return None
    judged = cached_score(row["platform"], row["job_id"])
    if judged is None:
        return "unjudged"
    return {
        "score": judged["score"],
        "tier": tier_for(judged["score"]),
        "shape": judged.get("shape"),
        "seniority": judged.get("seniority"),
        "domain": judged.get("domain"),
        "reason": judged.get("reason"),
        "company": row["company_slug"],
        "platform": row["platform"],
        "title": job_data["title"],
        "url": row.get("url"),
        "first_seen_at": row.get("first_seen_at"),
        "funding_hint": row.get("funding_hint"),
        "skills_mentioned": extracted_reqs.get("required_skills") or [],
        "years_required": extracted_reqs.get("years_experience_min"),
    }


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def print_table(results: list[dict], *, detail: bool) -> None:
    print(f"{'SCORE':>5}  {'TIER':<12}  {'SHAPE':<16}  {'COMPANY':<20}  TITLE")
    print("-" * 100)
    for r in results:
        print(
            f"{r['score']:>5}  {r['tier']:<12}  {str(r['shape'] or '-'):<16}  "
            f"{_truncate(r['company'], 20):<20}  {_truncate(r['title'], 40)}"
        )
        if detail:
            print(f"         {r['reason']}")
            print(
                f"         seniority={r['seniority']}  domain={r['domain']}  "
                f"years_required={r['years_required']}  "
                f"skills_mentioned={len(r['skills_mentioned'])}"
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
                        help="max postings to rank (default 25)")
    parser.add_argument("--min-score", type=int, default=0,
                        help="only show results at or above this score")
    parser.add_argument("--detail", action="store_true",
                        help="print the judge's rubric and reason per posting")
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

    results, unjudged, missing = [], [], 0
    for row in rows:
        scored = score_row(row, refresh=args.refresh)
        if scored is None:
            missing += 1
            continue
        if scored == "unjudged":
            unjudged.append(row)
            continue
        results.append(scored)

    results.sort(key=lambda r: r["score"], reverse=True)
    shown = [r for r in results if r["score"] >= args.min_score]

    if args.json:
        print(json.dumps(shown, indent=2))
        return 0

    print(
        f"\nQUALIFY — {len(results)} judged posting(s) ranked "
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
    if unjudged:
        print(f"\n{len(unjudged)} eligible posting(s) unjudged — no score, "
              f"not ranked. The outreach skill judges them; this read-only "
              f"view never calls the LLM:")
        for row in unjudged:
            print(f"  {row['company_slug']:<22} {_truncate(row.get('title') or '', 55)}")
    if missing:
        print(f"{missing} posting(s) ineligible or no longer on their board — skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
