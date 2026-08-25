# Implementation state

Source of truth for "is X actually built." Harvested code existing in
`harvest/` does **not** count as implemented — it has cross-project imports
that don't resolve standing alone (see `harvest/NOTES.md`). Code vendored
into `qualify/` with its imports fixed *does* count.

Every stage exists and is chained together: the `outreach` Claude Code
skill (`.claude/skills/outreach/SKILL.md`) drives `outreach_run.py`
through prepare → Agent 1 (contact-finder) → Agent 2 (drafter) → finalize.
It has run against live tracker data and produced real Gmail drafts, not
just per-stage spikes.

| Component | State | Note |
|---|---|---|
| Pipeline design | designed | `PIPELINE.md`, all 8 stages specified and decided |
| Harvest (3 source projects) | done | `harvest/`, see `harvest/NOTES.md` per-component |
| Execution model | **decided + implemented** | Claude Code-orchestrated on this Mac, human-triggered via the `outreach` skill — see `PIPELINE.md`. Ruled out: hosting it on the Pi (armv7l 32-bit, Node v10.15.2, no `claude` binary — Claude Code needs Node 18+ and doesn't target 32-bit ARM) |
| Orchestration (`outreach` skill + CLI) | **implemented** | `.claude/skills/outreach/SKILL.md` drives `outreach_run.py` (`prepare` / `judge` / `judge-save` / `finalize` / `status`, backed by `outreach/pipeline.py`). This is the glue between stages that was previously missing |
| QUALIFY gate | **implemented and discriminating** | `qualify/` + `qualify_run.py`. Deterministic dimensions plus a batched LLM semantic-fit judgement (`qualify/semantic.py`). Top of the ranking is now backend infrastructure and FDE roles; PM, presales, management and hardware roles fall out on their own. Tiers re-tuned 2026-08-24 from the judged sample (strong ≥72, spend bar 65 kept after measurement) — see `docs/qualify.md` |
| Resume/profile parsing | **done, by hand** | `qualify/profile.py`, written from `Yash_Bhavsar_Resume_08192026.pdf`. Instaply's `parser.py` deliberately not revived: one resume, four fields, no pipeline needed. All 46 skills resolve against `taxonomy.py` |
| Candidate feed | **implemented** | `qualify/candidates.py` — read-only SSH pull from the Pi's live `tracker.db`. Reproduces poll.py's NY/role predicates because `seen_jobs` holds every posting and `pending_alerts` is cleared after each alert email |
| Job descriptions | **implemented** | `qualify/boards.py` — fetched from the public Ashby/Greenhouse APIs (the tracker stores no description), cached per company for 24h |
| Agent 1 (find contact) | **implemented** | `.claude/agents/contact-finder.md`. Spiked 5/5 on live companies, two prompt defects found and fixed — see `docs/agents.md`, "Measured behavior" |
| Verify step (email check) | **implemented and live** | `outreach/verify.py`. Hunter email-verifier + domain-search pattern corroboration, cached per address, degrades to `unverified` on any failure. `HUNTER_API_KEY` is set and exercised against the real API. Since 2026-08-24 also cross-checks a candidate/fallback address against Hunter's per-address names (`_name_conflict`) before returning it, closing the identity gap the company-c near-miss exposed |
| Draft lint | **implemented** | `outreach/draft_lint.py`, run inside `finalize`. Checks sentence length, clause pileups, number count and implementation-vocabulary density; returns issues for the drafter to fix rather than silently passing weak drafts through |
| Agent 2 (draft email) | **implemented** | `.claude/agents/drafter.md`. Rewritten from Yash's real sent cold emails (voice, structure, no em dashes) — see `docs/agents.md`, "Agent 2 — the voice rewrite." Held the no-AI-tooling positioning rule under direct pressure |
| Human-review surface | **decided + implemented** | Gmail Drafts, via `outreach/gmail_draft.py` (IMAP APPEND). Replaced both the review surface and the send step — see `PIPELINE.md` stage 6+7 |
| `PROFILE.md` (story bank) | **implemented** | Curated from the Finaptive repo (`~/Code/Finaptive/App/finaptive-web-app`, 218/239 commits his), his sent cold emails, and the Paraform answers. ~1,700 words, deliberately small enough to load whole. Agent 2 reads it every draft; résumé facts are never pasted into prompts |
| Eligibility rules | **implemented** | `qualify/eligibility.py` — full-time only, no frontend-titled roles (title-only, parentheticals stripped, full-stack exempt — "Full-stack Engineer (React frontend, Python backend)" still qualifies), and since 2026-08-25 no stated minimum above his years (`check_years`, ceiling from `qualify/profile.py`'s `YEARS_OF_EXPERIENCE`). Title rules remove 14 of 200 postings in a 7-day window; the years rule dropped a same-day 33-posting pool to 11 |
| Prior-contact check | **implemented** | `outreach/history.py` — searches Gmail sent mail before drafting. The store starts empty and knew nothing about months of hand-written outreach; it let the pipeline draft the target contact at company-a three weeks after a real email had already gone to him at a personal domain, which a recipient-domain check would also have missed |
| Send step | **removed by design** | no code in this project can transmit a message. Sending is a human pressing Send in Gmail. `send_cold_email.py`'s message-building and attachment logic was reused; its SMTP call was not |
| DB schema (`pending_outreach`, `outreach_log`) | **implemented** | `outreach/store.py`, local `outreach.db` on the Mac (supersedes the `tracker.db` decision — see `docs/decisions.md`). Per-company dedup enforced by `outreach_log`'s primary key, not by caller discipline. Eight invariant cases tested: claim, double-claim refused, better-posting swap, worse-posting ignored, contact never re-looked-up, send closes the company, post-send draft refused, one log row per company |
| Agent 1's `source_notes` persisted | **implemented** | `pending_outreach.source_notes` column (bolted on via the existing `_ADDED_COLUMNS` migration pattern), populated through the `outreach` skill's finalize payload, rendered on each card in `outreach_ui.py`. Never reaches Agent 2 — storage only |
| `git init` | **done** | pushed to `github.com/yashexe/Adam` |

## What this means practically

The full pipeline runs end to end against live data. Semantic fit (the
dimension that used to make QUALIFY rank Product Owners above backend
engineers) is implemented and fixed the ranking — see `docs/qualify.md`.
The `outreach` skill has produced real outcomes, not just drafts: as of
2026-08-24, company-b was sent, company-a was correctly closed via the
prior-contact check without a duplicate, and five candidates (including
company-d) were reviewed and discarded — one of them, company-c, because
a human caught `resolve_address()` returning a real but wrong-person
mailbox (`wrong.mailbox@company-c.io` for the intended contact). That near-miss drove a fix
the same day — see below. Nothing is currently pending in Gmail.

What's actually left — see `CLAUDE.md`'s "Current priority" for the full,
ordered list:

- Whether the `outreach` skill should ever move from human-triggered to
  scheduled is open but not urgent — see `PIPELINE.md`'s execution-model
  section.
- The re-tuned tier cutoffs were validated same-day against a second
  ~180-posting sample: the 65 spend bar held (one judge-75 miss out of
  142 below it); the strong tier carries ~22% Staff-seniority-inflated
  composites, a known ordering caveat documented in `docs/qualify.md`.
