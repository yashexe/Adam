"""
Deterministic job-requirement extraction.

The heuristic half of Instaply's `matching/extractor.py`, vendored without
its LLM path: `heuristic_extract_requirements` never called a provider, so
it ports cleanly and keeps the QUALIFY gate fully deterministic. The LLM
extraction path and its `src.config.settings` dependency were left behind.

Since 2026-08-26 nothing scores on this output — the judge's reading is
the score (docs/qualify.md, "The judge becomes the score"). What it
extracts feeds the hard eligibility rules (`years_experience_min`,
`citizenship_required`, via `qualify/eligibility.py`) and the display
metadata shown next to a ranked posting; the skills lists are context for
a human, not points for a scorer.
"""

from __future__ import annotations

import re

from .taxonomy import extract_domains, extract_skill_hits, infer_role_family

EMPTY_RESULT: dict = {
    "role_family": None,
    "seniority": "unknown",
    "required_skills": [],
    "preferred_skills": [],
    "years_experience_min": None,
    "locations": [],
    "remote_policy": "unknown",
    "employment_type": "unknown",
    "salary_range": None,
    "visa_sponsorship": "unknown",
    "citizenship_required": False,
    "responsibilities": [],
    "domain_signals": [],
    "disqualifying_constraints": [],
}

def heuristic_extract_requirements(job_text: str) -> dict:
    """Extract a useful baseline without an LLM."""
    text = job_text.lower()
    result = dict(EMPTY_RESULT)

    skill_names = [name for name, _category in extract_skill_hits(job_text)]
    result["required_skills"] = skill_names

    preferred_section = ""
    preferred_match = re.search(
        r"(preferred|nice to have|bonus)(.*)",
        job_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if preferred_match:
        preferred_section = preferred_match.group(2).lower()
    if preferred_section:
        result["preferred_skills"] = [
            name for name, _category in extract_skill_hits(preferred_section)
        ]

    # Range first: "3-5 years" would otherwise match on the plain pattern
    # below at "5 years" (the first number has no space before the dash),
    # silently taking the range's ceiling as its floor.
    year_match = re.search(r"(\d+)\s*(?:-|–|to)\s*\d+\+?\s*years?", text)
    if not year_match:
        year_match = re.search(r"(\d+)\+?\s+years?", text)
    if year_match:
        result["years_experience_min"] = int(year_match.group(1))

    if any(word in text for word in ("staff", "principal")):
        result["seniority"] = "staff" if "staff" in text else "principal"
    elif any(word in text for word in ("senior", "sr.")):
        result["seniority"] = "senior"
    elif any(word in text for word in ("junior", "entry level", "entry-level")):
        result["seniority"] = "junior"

    if "remote" in text:
        result["remote_policy"] = "remote"
    if "hybrid" in text:
        result["remote_policy"] = "hybrid"
    if any(word in text for word in ("onsite", "on-site", "in office", "in-office")):
        result["remote_policy"] = "onsite"

    if any(
        phrase in text
        for phrase in (
            "visa sponsorship is not mentioned",
            "sponsorship is not mentioned",
        )
    ):
        result["visa_sponsorship"] = "unknown"
    elif any(
        phrase in text
        for phrase in ("no visa", "unable to sponsor", "do not sponsor")
    ):
        result["visa_sponsorship"] = "no"
    elif any(phrase in text for phrase in ("will sponsor", "sponsorship available")):
        result["visa_sponsorship"] = "yes"

    # Separate from visa_sponsorship on purpose: "no sponsorship" alone is
    # not disqualifying (he may be TN-eligible, and plenty of companies say
    # it without meaning to rule out TN) -- only an explicit citizenship
    # requirement is. See qualify/eligibility.py's check_citizenship_required.
    # Postings write the nationality as "US", "U.S.", or "U.S" -- normalize
    # before matching (a company-e FDE posting's "Must be a U.S. citizen"
    # slipped past the plain "us" spelling on 2026-08-28).
    citizenship_text = re.sub(r"\bu\.s\.?(?=\s)", "us", text)
    if any(
        phrase in citizenship_text
        for phrase in ("citizenship required", "must be a us citizen", "us citizens only")
    ):
        result["citizenship_required"] = True

    result["role_family"] = infer_role_family(job_text)
    result["domain_signals"] = extract_domains(job_text)

    return result
