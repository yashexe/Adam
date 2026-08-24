"""
Hard eligibility rules, applied before scoring.

These are not quality heuristics and not an attempt to raise precision.
They are two facts about what Yash is available for, and a posting that
fails either is not a weak match, it is not a match at all:

1. **Full-time only.** He is not an intern.
2. **Not frontend-titled roles.** He does not enjoy frontend work and does
   not apply to roles with "Frontend" in the title. Frontend as one
   component of a broader role is fine and describes most jobs, so this
   tests the *title* only, never the description.

This sits uneasily beside the project rule against exclude filters (see
CLAUDE.md), which exists because a keyword blocklist quietly eats relevant
postings. That rule is about precision tradeoffs on ambiguous matches.
These two are unambiguous eligibility facts stated directly by the user, so
they are implemented, kept deliberately tiny, and tested against the title
alone. Do not grow this file into a general-purpose blocklist.
"""

from __future__ import annotations

import re

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


def is_eligible(title: str) -> bool:
    return check_title(title)[0]
