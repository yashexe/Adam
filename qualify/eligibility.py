"""
Hard eligibility rules, applied before scoring.

These are not quality heuristics and not an attempt to raise precision.
They are facts about what Yash is available for, and a posting that fails
any of them is not a weak match, it is not a match at all:

1. **Full-time only.** He is not an intern.
2. **Not frontend-titled roles.** He does not enjoy frontend work and does
   not apply to roles with "Frontend" in the title. Frontend as one
   component of a broader role is fine and describes most jobs, so this
   tests the *title* only, never the description.
3. **Not a stated minimum above his years.** Confirmed directly by Yash
   2026-08-25: 2.5-3 years counting everything he's done, and a posting
   naming 4+ isn't a stretch worth scoring — "at that point the range
   doesn't even matter." A posting stating no minimum is eligible; absence
   of a number is not a claim he falls short of one.
4. **Not a stated US citizenship requirement.** Confirmed directly by Yash
   2026-08-25, reacting to a real posting (company-e) stating "US citizenship
   required": "if they ask US citizenship theres no point moving forward."
   Narrower than "no sponsorship" on purpose, per Yash: a posting saying
   only that is still eligible — he may be TN-eligible and companies say
   "no sponsorship" without meaning to rule that route out; "ONLY if they
   mention US citizenship, should we back off."

This sits uneasily beside the project rule against exclude filters (see
CLAUDE.md), which exists because a keyword blocklist quietly eats relevant
postings. That rule is about precision tradeoffs on ambiguous matches.
These are unambiguous eligibility facts stated directly by the user, so
they are implemented and kept deliberately tiny. The first two are tested
against the title alone; the third and fourth need the posting body, so
they run after the board fetch rather than before it. Do not grow this
file into a general-purpose blocklist.
"""

from __future__ import annotations

import re

from .profile import YEARS_OF_EXPERIENCE

# Word-boundary matched against the title. "intern" must not fire on
# "internal" or "international".
_INTERN_RE = re.compile(r"\b(intern|interns|internship|co-?op)\b", re.IGNORECASE)
_FRONTEND_RE = re.compile(r"\bfront[\s-]?end\b", re.IGNORECASE)
# A parenthetical usually lists the stack rather than naming the role:
# "Full-stack Engineer (React frontend, Python backend)" is a role he wants.
_PAREN_RE = re.compile(r"\([^)]*\)")
_FULLSTACK_RE = re.compile(r"\bfull[\s-]?stack\b", re.IGNORECASE)


def check_title(title: str) -> tuple[bool, str]:
    """(eligible, reason). Reason is empty when eligible.

    Only the role-naming part of the title is tested for frontend: anything
    inside parentheses is stripped first, and an explicit full-stack title
    wins outright. Both carve-outs exist because frontend being *one part*
    of a role is fine, and only frontend being *the* role is not.
    """
    text = title or ""
    if _INTERN_RE.search(text):
        return False, "internship or co-op posting; full-time roles only"

    role_part = _PAREN_RE.sub(" ", text)
    if _FULLSTACK_RE.search(role_part):
        return True, ""
    if _FRONTEND_RE.search(role_part):
        return False, "frontend names the role; frontend as one part of a role is fine"
    return True, ""


def check_years(years_experience_min: int | None) -> tuple[bool, str]:
    """(eligible, reason). See rule 3 above. Reuses profile.py's
    YEARS_OF_EXPERIENCE rather than a second hardcoded 3, so a future
    resume update only has to change one number."""
    if years_experience_min is not None and years_experience_min > YEARS_OF_EXPERIENCE:
        return False, f"wants {years_experience_min}+ years, he has {YEARS_OF_EXPERIENCE}"
    return True, ""


def check_citizenship_required(citizenship_required: bool) -> tuple[bool, str]:
    """(eligible, reason). See rule 4 above. Deliberately does not look at
    general "no visa sponsorship" language -- only an explicit citizenship
    requirement disqualifies."""
    if citizenship_required:
        return False, "states US citizenship required"
    return True, ""


def is_eligible(title: str) -> bool:
    return check_title(title)[0]
