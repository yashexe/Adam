"""
Semantic fit — the 30-weight dimension, supplied by an LLM judgment.

Instaply computed this with a local `sentence-transformers` model. That was
rejected here: it drags torch in as a dependency for one number, and the
execution model already has a model in the loop for stages 3 and 5. What
this dimension actually needs is a judgement about whether a posting is the
kind of job this person should be contacted about, and that is a reading
task, not a distance calculation.

Measured on 58 live postings, leaving it unscored made the gate rank an
intern requisition first and a frontend design-systems role second. See
docs/qualify.md, "Measured behavior".

## Why the score is mapped rather than passed through

`scorer._score_semantic_fit` expects a cosine similarity and rescales it
between SEMANTIC_SIM_FLOOR (0.25) and SEMANTIC_SIM_CEIL (0.55), a band
calibrated against all-MiniLM-L6-v2 on Instaply's corpus. Feeding a 0-1
judgement straight in would put almost everything at the ceiling.

So the judge returns a plain 0-100 relevance score and `to_similarity`
maps it onto that band, which makes `_score_semantic_fit` compute
`ratio = llm_score / 100` exactly. The scorer stays a verbatim port with no
change at all, and the calibration lives here where it is visible.

## Batching

One call scores the whole day. Sixty separate calls would cost sixty times
the overhead to answer the same question, and judging postings side by side
produces more consistent scores than judging them in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

from qualify.scorer import SEMANTIC_SIM_CEIL, SEMANTIC_SIM_FLOOR

CACHE_PATH = Path(__file__).resolve().parent.parent / ".cache" / "semantic.json"

# Enough of the posting to judge relevance without paying for the whole
# thing. The opening of a description is nearly always the "about the role"
# section; requirements further down are already covered by the
# deterministic skills dimensions.
DESCRIPTION_CHARS = 800


def to_similarity(llm_score: int | float) -> float:
    """Map a 0-100 relevance judgement onto the scorer's similarity band."""
    ratio = max(0.0, min(100.0, float(llm_score))) / 100.0
    return SEMANTIC_SIM_FLOOR + ratio * (SEMANTIC_SIM_CEIL - SEMANTIC_SIM_FLOOR)


def _key(platform: str, job_id: str) -> str:
    return f"{platform}:{job_id}"


def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_scores(scores: list[dict]) -> int:
    """Persist judged postings. Each entry: {platform, job_id, score, reason}."""
    cache = load_cache()
    for entry in scores:
        platform, job_id = entry.get("platform"), str(entry.get("job_id", ""))
        if not platform or not job_id or entry.get("score") is None:
            continue
        cache[_key(platform, job_id)] = {
            "score": int(entry["score"]),
            "reason": entry.get("reason", ""),
        }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2))
    return len(cache)


def cached_similarity(platform: str, job_id: str) -> float | None:
    """Similarity for `score_job`, or None if this posting is unjudged."""
    entry = load_cache().get(_key(platform, str(job_id)))
    return to_similarity(entry["score"]) if entry else None


def cached_score(platform: str, job_id: str) -> dict | None:
    return load_cache().get(_key(platform, str(job_id)))


def unjudged(postings: list[dict]) -> list[dict]:
    cache = load_cache()
    return [
        p for p in postings
        if _key(p["platform"], str(p["job_id"])) not in cache
    ]


def build_batch(postings: list[dict]) -> str:
    """The block of postings handed to the judge."""
    blocks = []
    for i, p in enumerate(postings, 1):
        description = (p.get("description_text") or "")[:DESCRIPTION_CHARS].strip()
        blocks.append(
            f"### {i}. platform={p['platform']} job_id={p['job_id']}\n"
            f"company: {p.get('company_slug', '?')}\n"
            f"title: {p.get('job_title') or p.get('title')}\n"
            f"department: {p.get('department') or '-'}\n"
            f"description (first {DESCRIPTION_CHARS} chars):\n{description}\n"
        )
    return "\n".join(blocks)
