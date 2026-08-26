"""
The judge — the fit score, supplied by an LLM judgement.

Until 2026-08-26 this module fed one dimension (weight 30) of a 100-point
composite inherited from Instaply, mapping the judge's 0-100 onto a cosine
similarity band the deterministic scorer expected. Measurement killed that
architecture (docs/qualify.md, "The judge becomes the score"): on a
305-posting corpus every deviation the deterministic 70 points produced
against the judge was an error in the same direction, real interview
outcomes ordered 7-for-7 on the judge alone, and a re-judged stability
sample held a +0.97 test-retest correlation. The composite was a lossy
copy of the judge, so the copy was deleted and the judge's 0-100 IS the
QUALIFY score. Eligibility stays deterministic (facts, not fit);
extraction stays for eligibility inputs and display metadata.

Each judgement carries a small rubric besides the score — shape /
seniority / domain plus a one-line reason — so the ranking and review
surfaces can show *why* without re-reading the posting.

## Anchors

Every batch carries three frozen calibration postings (`anchors.py`),
scored blind alongside the live ones. `save_scores` checks them against
their expected bands and warns on a miss instead of caching them —
drift detection for a score that now carries everything.

## Batching

One call scores the whole day. Sixty separate calls would cost sixty
times the overhead to answer the same question, and judging postings side
by side produces more consistent scores than judging them in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

from qualify.anchors import ANCHOR_PLATFORM, ANCHORS

CACHE_PATH = Path(__file__).resolve().parent.parent / ".cache" / "semantic.json"

# How much of the posting the judge reads. 800 until 2026-08-26 — enough
# for "about the role" but blind to requirements sections further down,
# which mattered little while deterministic dimensions covered skills and
# matters a lot now that the judge is the whole score.
DESCRIPTION_CHARS = 3000

# Rubric vocabularies. Stored as given (display, not control flow), listed
# here so the judge prompt and any consumer agree on the words.
SHAPES = ("core-engineering", "forward-deployed", "customer-facing",
          "research", "management", "non-engineering")
SENIORITY = ("fits", "stretch", "above")
DOMAIN = ("strong", "some", "none")


def _key(platform: str, job_id: str) -> str:
    return f"{platform}:{job_id}"


def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_scores(scores: list[dict]) -> tuple[int, list[str]]:
    """Persist judged postings; validate and drop anchors.

    Each entry: {platform, job_id, score, shape, seniority, domain, reason}.
    Returns (total cached, anchor warnings). An anchor scoring outside its
    expected band is a warning the caller must surface, not an error — the
    live judgements are still saved, but a human should know the judge has
    moved before trusting the batch.
    """
    warnings: list[str] = []
    expected = {a["name"]: a["expect"] for a in ANCHORS}
    cache = load_cache()
    for entry in scores:
        platform, job_id = entry.get("platform"), str(entry.get("job_id", ""))
        if not platform or not job_id or entry.get("score") is None:
            continue
        if platform == ANCHOR_PLATFORM:
            lo, hi = expected.get(job_id, (0, 100))
            score = int(entry["score"])
            if not lo <= score <= hi:
                warnings.append(
                    f"anchor {job_id} scored {score}, expected {lo}-{hi} — "
                    f"the judge has drifted; treat this batch's scores "
                    f"with suspicion"
                )
            continue
        cache[_key(platform, job_id)] = {
            "score": int(entry["score"]),
            "shape": entry.get("shape"),
            "seniority": entry.get("seniority"),
            "domain": entry.get("domain"),
            "reason": entry.get("reason", ""),
        }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2))
    return len(cache), warnings


def cached_score(platform: str, job_id: str) -> dict | None:
    """The full cached judgement for one posting, or None if unjudged."""
    return load_cache().get(_key(platform, str(job_id)))


def unjudged(postings: list[dict]) -> list[dict]:
    cache = load_cache()
    return [
        p for p in postings
        if _key(p["platform"], str(p["job_id"])) not in cache
    ]


def build_batch(postings: list[dict], *, include_anchors: bool = True) -> str:
    """The block of postings handed to the judge, anchors mixed in.

    Anchors ride unlabelled — the judge cannot tell them from live
    postings, which is the point.
    """
    rows = list(postings)
    if include_anchors:
        rows = rows + [
            {"platform": ANCHOR_PLATFORM, "job_id": a["name"],
             "company_slug": a["company_slug"], "job_title": a["job_title"],
             "department": None, "description_text": a["description_text"]}
            for a in ANCHORS
        ]
    blocks = []
    for i, p in enumerate(rows, 1):
        description = (p.get("description_text") or "")[:DESCRIPTION_CHARS].strip()
        blocks.append(
            f"### {i}. platform={p['platform']} job_id={p['job_id']}\n"
            f"company: {p.get('company_slug', '?')}\n"
            f"title: {p.get('job_title') or p.get('title')}\n"
            f"department: {p.get('department') or '-'}\n"
            f"description (first {DESCRIPTION_CHARS} chars):\n{description}\n"
        )
    return "\n".join(blocks)
