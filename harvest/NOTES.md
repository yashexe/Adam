# Harvest — raw materials, not a working system

Coarse-grained extraction from three prior projects, done 2026-08-22. This
is the torso, not a transplant: nothing here is wired together or expected
to run as-is. Pre-implementation staging so the source material survives
without re-deriving it from three different repos later.

Status vocabulary used below: **ported** (used close to unchanged),
**adapted** (the idea/logic is used, not the file itself), **reference-only**
(kept for context, not expected to be used directly).

## from_job_search_automation/

| File | Purpose | Status | Limitations |
|---|---|---|---|
| `send_cold_email.py` | SMTP send + resume attachment | **ported** — the one deterministic piece reused close to unchanged, becomes stage 7 | hardcoded single `target_email`/`html_content` per run; needs generalizing to take a draft from `pending_outreach` |
| `README.md` | Drafting rules (brief, founder-focused, infra metrics, no AI-tool mentions) + "resume shift" positioning | **adapted** — becomes Agent 2's prompt basis, see `docs/agents.md` | written as instructions to a human/agent doing this manually per-company; needs reshaping into a system prompt |
| `Yash_Bhavsar_Resume_08192026.pdf` | Current resume | **ported**, kept current | replaced 2026-08-22 (was `_07082026`); `send_cold_email.py`'s hardcoded path updated to match |

Not copied: `.env` (real Gmail credentials) — reconfigure fresh when this
becomes real, don't reuse the file.

## from_instaply/

Instaply's `src/matching/` + `src/profile/` + `src/common/taxonomy.py`,
copied flat. Not copied: the FastAPI app, routers, or DB repository layer —
wired to Instaply's own schema and HTTP surface, not reusable as-is.

| File | Purpose | Status | Limitations |
|---|---|---|---|
| `matching/scorer.py` | Weighted deterministic scorer — semantic/role/skills/experience/preferences/domain dimensions + non-compensatory skills gate | **adapted** — the real find, canonical description now in `docs/qualify.md` | needs a `structured_profile` input it doesn't itself produce (see `profile/parser.py` below) |
| `matching/judge.py` | LLM judge: blends its own fit score 60/40 with the deterministic one; has the threshold-gated draft trick (`_letter_fit_threshold`) | **adapted** — the blend and the threshold-gating shape are reused, see `docs/qualify.md` and `docs/agents.md` | imports `src.llm.factory.get_llm_provider()`, not copied (Instaply's multi-provider/budget/cooldown plumbing) — a real pattern worth reusing conceptually once the execution model is picked, not before |
| `matching/explainer.py`, `extractor.py`, `filters.py`, `embeddings.py`, `models.py`, `service.py` | Rest of the matching pipeline (hard filters, requirement extraction, semantic embeddings, orchestration) | **reference-only** | grabbed for completeness; not currently expected to be used directly |
| `profile/parser.py`, `profile/models.py` | Resume → structured profile (skills, roles, years, domains) | **adapted, blocking** | this is what needs to run against the current resume — see `docs/qualify.md`'s freshness requirement. Has Instaply-relative imports, won't run standing alone yet |
| `common/taxonomy.py` (`canonical_skill`) | Skill synonym resolution (node ↔ Node.js, postgres ↔ PostgreSQL) | **ported** — small, dependency-light | none known |
| `docs/matching-and-scoring.md` | Original design doc | **reference-only** | superseded by `docs/qualify.md` as the canonical description |
| `sample_real_matches.json` (local-only, not committed) | 8 real, non-template LLM judgments from Instaply's actual run — scores, reasoning, two full cover letters | **reference-only** | calibration examples for Agent 2's prompt, not code |

All matching/profile files have Instaply-relative imports (`from
src.matching...`, `from src.config import settings`) and will not run
standing alone. Expected — this is source material, not a package.

## from_paraform_pipeline/

Scripts and compact outputs from the Paraform scrape-and-enrich pipeline
found in `~/Code/job-search-help/tmp/`. **Not being integrated now** —
Ashby/Greenhouse stay the only discovery sources (see `docs/decisions.md`).
Everything below is **reference-only**.

| File(s) | Purpose | Limitations |
|---|---|---|
| `fetch_all_ny.py`, `fetch_target_roles.py`, `fetch_raw_targeted_roles.py`, `fetch_raw.py`, `fetch_ny_roles.sh`, `fetch_roles_restore.sh` | Scrape layer | needs `beautifulsoup4` (not vendored here, `pip install` fresh) and a live authenticated Paraform session — session/cookie handling was never in these files, it lived in `applicantpage.har`/`current_user.json`, neither copied (see below) |
| `build_rich_markdown.py`, `format_csv.py`, `format_md.py`, `format_ny_csv.py`, `format_ny_full.py`, `parse_paraform.py` | Normalization/enrichment — produced the per-role markdown format with interview-process steps, salary, equity, visa, work policy | richer than either ashby-ny-tracker or Instaply capture today, if this ever gets revived |
| `check_roles_count.py`, `find_company.py` | Small utility scripts | — |
| `find_recruiter_info.py` | Extracts Paraform's internal recruiter-facing metadata (role/company "selling points," recruiter-only screening questions) | **misleadingly named** — not a contact-finder, doesn't return names or emails. Useful for drafting hooks, not for Agent 1 |
| `match_jobs.py`, `rank_roles.py`, `score_cats.py` | A second, separate scoring/ranking system built against the Paraform data (`score_cats.py` dated 2026-08-21, a day before this harvest) | a **fourth** instance of "strong role" logic across these projects, distinct from Instaply's. Not reconciled with `docs/qualify.md`'s scorer — only matters if Paraform integration is revived |
| local scrape-output ranking samples (not committed) | Distilled output samples | shows what this system's output looked like |
| `paraform_ny_roles.csv`, `paraform_ny_roles_detailed.csv` | 443 NY roles, compact structured form | salary, visa text, interview timeline, tech stack |
| `ny_roles_mds/` | 444 per-role markdown files, full detail incl. complete job descriptions and interview steps | 2MB, copied in full rather than sampled |

**Not copied, deliberately** (bulk, not signal — regenerable by rerunning
the fetch scripts against a fresh session): `ny_roles_raw/` (886 files),
`scraped_roles/`, `scraped_roles_raw/`, `scraped_roles_rich/`,
`getAllRoles.json` (1.4MB), `ny_roles.json` (540KB), `jobs.json`,
`company-m_raw.json`, `pdfs/`.

## Deliberately excluded everywhere (credential/session-adjacent)

- `job_search_automation/.env` — real Gmail app password.
- `job-search-help/applicantpage.har` — captured browser network traffic,
  likely contains Paraform session/auth data.
- `job-search-help/tmp/current_user.json` — personal Paraform account data
  (name, email, account ID).
- `job-search-help/tmp/my_applications.json` — personal application history.

None of these are needed to read or reason about the harvested code. If
the Paraform pipeline is ever revived, all four need to be regenerated
fresh (new `.env`, new authenticated session), not copied from their
original locations into a less-protected staging folder.
