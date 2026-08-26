"""
The one profile fact deterministic code still consumes.

Until 2026-08-26 this file carried the full scoring profile — 46 skills,
target roles, domains, preferences — consumed by the Instaply-inherited
composite in `scorer.py`. That composite was deleted when measurement
showed it was a lossy copy of the judge (docs/qualify.md, "The judge
becomes the score"), and the judge reads `PROFILE.md` at the repo root,
not this file. What remains is the single number the hard eligibility
rules need. Re-derive by hand when the resume changes.
"""

from __future__ import annotations

# Confirmed directly by Yash 2026-08-25: 2.5-3 years counting everything
# (AMD May 2023-Apr 2024, Finaptive intern Feb-May 2025, Finaptive
# full-time Jun 2025-present). This is the ceiling
# `qualify/eligibility.py`'s check_years uses to hard-exclude postings
# wanting 4+; the judge prompt states the same numbers in prose and must
# be updated together with this ("Seniority" in
# .claude/agents/relevance-judge.md).
YEARS_OF_EXPERIENCE = 3
