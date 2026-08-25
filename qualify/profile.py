"""
The candidate profile and preferences the QUALIFY gate scores against.

Hand-written from `harvest/from_job_search_automation/Yash_Bhavsar_Resume_08192026.pdf`
(the current resume, 2026-08-22) rather than produced by Instaply's
`profile/parser.py`. The parser is a resume-parsing pipeline; there is one
resume and four fields, so the pipeline is infrastructure for a problem
that doesn't exist here. Re-derive by hand when the resume changes.

Skill and domain strings are deliberately drawn from `taxonomy.py`'s
vocabulary — `canonical_skill()` only resolves names it knows, so a skill
spelled off-vocabulary silently scores as absent.

--- TUNABLES ---------------------------------------------------------------
Three values below are judgment calls, not facts read off the resume. They
move scores materially, so they are marked and grouped rather than buried:
YEARS_OF_EXPERIENCE, DOMAINS, and NEEDS_VISA_SPONSORSHIP.
"""

from __future__ import annotations

# Confirmed directly by Yash 2026-08-25: 2.5-3 years counting everything
# (AMD May 2023-Apr 2024, Finaptive intern Feb-May 2025, Finaptive
# full-time Jun 2025-present). 3 was the resume-inferred estimate this
# replaced a defensible-middle guess of 2 with; it's also the ceiling
# `qualify/eligibility.py`'s check_years uses to hard-exclude postings
# wanting 4+. It matters for scoring too: `_score_experience_fit` compares
# this directly against a posting's stated minimum, and a posting asking
# 5+ years scores 0.33 at 3 versus 0.13 at 2.
YEARS_OF_EXPERIENCE = 3

# Must be keys of taxonomy.DOMAIN_KEYWORDS — `_score_domain_company_fit`
# scores matched/len(DOMAINS), so every domain added dilutes the ratio for
# postings that don't match it. "Hardware Automation" and "Developer
# Tooling" are both true of the resume (AMD) but are not where this search
# is pointed, so they are left out on purpose.
DOMAINS = [
    "FinTech",
    "Data Engineering",
    "Backend Systems",
    "Cloud Infrastructure",
    "AI/ML",
    "Security",
]

# Left False, which awards the visa sub-dimension's 2 points unconditionally.
# Set True only if sponsorship is actually required: it makes postings that
# explicitly say "we do not sponsor" lose those points, and postings silent
# on the question drop out of the denominator entirely.
NEEDS_VISA_SPONSORSHIP = False
# ---------------------------------------------------------------------------

SKILLS = [
    # Languages
    "Python", "JavaScript", "TypeScript", "C/C++", "PHP", "C Shell",
    # Frameworks and runtime
    "Flask", "Celery", "Celery RedBeat", "Asyncio", "Pydantic", "React",
    # Data and messaging
    "PostgreSQL", "Redis", "MongoDB", "MySQL", "ODBC",
    # AI/ML
    "PyTorch", "Machine Learning", "Reinforcement Learning", "PPO",
    "Actor-Critic", "Generalized Advantage Estimation", "Transformers",
    "NLP", "LLM",
    # Cloud, infra, DevOps
    "Azure", "Azure Key Vault", "Azure Container Registry", "Docker",
    "Containerization", "GitHub Actions", "CI/CD", "Linux",
    # Integration and security
    "REST APIs", "OAuth2", "mTLS",
    # Architecture
    "Distributed Systems", "Distributed Mutexes", "Rate Limit Handling",
    "Concurrency", "Scalable Architecture",
    # Data engineering
    "ETL", "Data Pipelines",
    # Tooling
    "Static Analysis", "Real-Time Dashboards",
]

ROLES = [
    "Software Engineer",
    "Software Engineer Intern",
    "Design Automation Engineer",
]

# `scorer.score_job` reads the profile through `_profile_data`, which pulls
# the "structured_profile" key (dict or JSON string).
PROFILE: dict = {
    "structured_profile": {
        "skills": SKILLS,
        "roles": ROLES,
        "years_of_experience": YEARS_OF_EXPERIENCE,
        "domains": DOMAINS,
    }
}

# Roles worth being contacted about. Scored by `_score_role_title_fit`
# against the posting title, so these are target titles, not a description
# of past work.
#
# Scoped to backend and forward-deployed work, which is what the experience
# actually supports: data connectors, ERP and EHR integrations, distributed
# queueing, plus the client-embedded integration work at Finaptive that is
# forward-deployed engineering in everything but title.
#
# Dropped from the earlier list: "AI Engineer" and "Machine Learning
# Engineer" (the AI work here is applied LLM infrastructure inside a backend
# system, not model work, and matching ML titles pulled in research-shaped
# roles), and "Product Engineer" (too broad to mean anything).
#
# Also dropped: a bare "Platform Engineer". `_score_role_title_fit` awards
# full marks on a substring match, so it matched "Senior Frontend Platform
# Engineer, Design Systems" and handed a frontend design-systems role 15/15.
# "Infrastructure Engineer" covers the same intent without colliding. This
# is a real tradeoff, not a clean win: a genuine "Platform Engineer" posting
# now scores on word overlap rather than an exact match.
TARGET_ROLES = [
    "Software Engineer", "Backend Engineer", "Infrastructure Engineer",
    "Data Engineer", "ETL Engineer", "Full Stack Engineer",
    "Forward Deployed Engineer", "Solutions Engineer", "Solutions Architect",
    "Integration Engineer", "Implementation Engineer", "Deployment Engineer",
    "Founding Engineer",
]

PREFERENCES: dict = {
    "target_roles": TARGET_ROLES,
    "locations": ["New York"],
    # "any" awards the remote sub-dimension in full. The candidate pool is
    # already NY-filtered upstream, so remote policy is not a live constraint.
    "remote_policy": "any",
    # None awards the salary sub-dimension in full. Neither board API gives
    # a reliable salary ceiling, so a real threshold here would mostly
    # measure which postings happen to publish a range.
    "min_salary": None,
    "needs_visa_sponsorship": NEEDS_VISA_SPONSORSHIP,
}
