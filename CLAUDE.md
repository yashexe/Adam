# CLAUDE.md — Adam

Primary context for working on this project. Read this first, every
session — it's the only file that should need to be.

**The name.** From *The Creation of Adam*: two hands reaching across a
small gap. That gap is the one between a company realising it needs
someone and Yash reaching the person who can act on it. Everything here
exists to close it before the queue forms — which is also why speed and
directness beat coverage everywhere the two conflict.

## What this is

An agentic pipeline that turns a fresh ashby-ny-tracker job match into a
drafted, personalized cold-outreach email, ready for a human to send.
ashby-ny-tracker finds NY postings fast, before the flood of applicants;
this project reaches the hiring contact directly instead of joining the
ATS queue — a more direct lever toward the same goal (see
ashby-ny-tracker's `feedback-prioritize-speed-to-posting` memory). It
supersedes `job_search_automation` (a manual, single-target script) rather
than extending it, and lives in its own directory because it needs an LLM
in the loop, which ashby-ny-tracker deliberately has none of.

## Architecture

```
[1] Trigger    det.      fresh match from ashby-ny-tracker's poll.py
[2] Qualify    det.      Instaply-harvested scoring gate — docs/qualify.md
[3] Find contact  AGENT  Agent 1 — docs/agents.md
[4] Verify     det.      Hunter.io email verification, live — outreach/verify.py
[5] Draft      AGENT     Agent 2 — docs/agents.md
[6] Approve    det./human   the one gate that can't be automated away
[7] Send       det./human   reused job_search_automation SMTP code
[8] Log        det.      company-level dedup
```

Who Yash is, and every story a draft can draw on: `PROFILE.md` — the single
place his background is maintained. Agent 2 reads it for every draft, so
changing a fact there changes all future drafts. Never paste résumé facts
into a prompt.

Full depth: `PIPELINE.md`. Subsystem contracts: `docs/agents.md` (Agent 1/2),
`docs/qualify.md` (the QUALIFY gate). Where every harvested piece of code
came from: `harvest/NOTES.md`. Why each fork was resolved the way it was:
`docs/decisions.md`. What's actually built vs. designed: `docs/status.md`.

## Non-negotiable invariants

- Dedup is per-company, never per-posting or per-contact.
- Nothing sends without live, explicit human/chat approval — no exception,
  regardless of how confident any agent is.
- Outreach is independent of applying — never gated behind "did I apply."
- No sector filter layered on top of the QUALIFY score. The two hard
  eligibility rules in `qualify/eligibility.py` (full-time only, no
  frontend-titled roles) are the deliberate exception and must stay tiny.
- Data lives in ashby-ny-tracker's `tracker.db` directly — no separate DB.
- Contact targets are ranked by who is likeliest to reply, not by
  seniority. Recruiters are a first-class target, second only to a named
  hiring manager — reading candidate mail is their job rather than an
  interruption from it. They were banned outright until 2026-08-24 on the
  reasoning that this pipeline routes around the ATS; that confused the
  channel with the person. Shared inboxes (`careers@`) are still refused: a
  named recruiter is a person, an alias is a queue.
- A claim is never released without checking Gmail's Sent folder first.
  `outreach.db` records intent; only Gmail knows what a human actually did
  with a draft. A sent-but-unlogged company looks exactly like an abandoned
  one, and releasing it would let the pipeline write a second cold email to
  someone who already got one — see `outreach/reconcile.py`.
- Agent 1 never writes persuasive copy; Agent 2 never judges whether a
  contact is real. Strict separation, enforced by the deterministic verify
  step between them.
- Deterministic code owns anything with lasting real-world consequence;
  agentic work owns only what's tedious-but-cheap-to-retry.
- ashby-ny-tracker's and Instaply's own ingestion/serving layers are not
  revived — this project does not duplicate discovery or stand up a
  second running service.

## Current status

All eight stages are implemented and have run end to end against live
data, orchestrated by the `outreach` Claude Code skill
(`.claude/skills/outreach/SKILL.md`) driving `outreach_run.py` plus the
`contact-finder` and `drafter` subagents. Semantic fit (QUALIFY's largest
dimension) is implemented as a batched LLM judgement, which fixed the
gate: the top of the ranking went from product-management and intern
postings to backend infrastructure and forward-deployed roles. Numbers in
`docs/qualify.md`. Full breakdown: `docs/status.md`.

The pipeline has already produced real Gmail drafts against live tracker
matches and correctly closed out a company via the prior-contact check
(company-a, previously emailed by hand) without duplicating it.

## Current priority

1. **Verification cannot confirm identity** [FIXED 2026-08-24] —
   `resolve_address()` used to check only that a mailbox was deliverable,
   never that it belonged to the person Agent 1 named. Applying
   company-c.io's `{first}{l}` pattern to the intended contact produced
   `wrong.mailbox@company-c.io`, a real mailbox verified at score 100 belonging to
   someone else. Caught by hand before sending. Fix: `confirm_pattern()`
   now returns the per-address names from Hunter's domain-search response
   instead of discarding them, and `_name_conflict()` (`outreach/verify.py`)
   checks a candidate or fallback address against that list before
   `resolve_address` will return it — a rendered or observed address that
   Hunter already attributes to a different name is refused, the same way
   a role account already was.
2. **Agent 1's reasoning is discarded** [FIXED 2026-08-24] — `source_notes`
   explains which sources were used and what stayed uncertain; nothing
   stored it, so a draft could not be reviewed for *why* that contact was
   chosen. Fix: `pending_outreach.source_notes` (`outreach/store.py`), the
   `outreach` skill's finalize payload now always includes it
   (`SKILL.md` step 4), and it's rendered on each card in the review UI
   (`outreach_ui.py`) — visible without reaching Agent 2, which still never
   sees it.
3. **Tier cutoffs** [FIXED 2026-08-24] — re-tuned empirically from the
   joint composite/judge distribution over the 56-posting judged sample:
   strong is now 72 (Instaply's 85 sat above the entire observed
   distribution — max composite is 83), and the 65 spend bar was kept
   deliberately after measuring it, no longer inherited. Full derivation:
   `docs/qualify.md`, "Where the cutoffs come from".
4. **Execution model** [DECIDED] — Claude Code-orchestrated on this Mac,
   human-triggered via the `outreach` skill, reusing the existing
   subscription rather than a new metered API. Built and running, not just
   leaned toward — see `PIPELINE.md`'s Execution model section. Whether it
   should ever move to an unattended/scheduled trigger instead of
   chat-triggered is still open, but not urgent. Note the Pi cannot host
   it either way: armv7l 32-bit, Node v10, no `claude` binary.
5. **`git init`** [DECIDED] — done. Pushed to `github.com/yashexe/Adam`.

The profile no longer blocks anything — it is hand-written in
`qualify/profile.py` from the current resume, and Instaply's `parser.py` was
not revived.

## Decisions not to reopen

Full reasoning for each: `docs/decisions.md`.

| Decision | One-line why |
|---|---|
| Outreach independent of applying | gating behind an application reintroduces the delay the tracker exists to remove |
| Dedup keyed on company alone | prevents multiple emails to the same person from the same company |
| Two agents, not one | research and persuasive writing are different skills; the verify step needs a clean structured boundary between them |
| Contact-finding is agentic, not API-only | Hunter/Apollo coverage collapses on <50-employee companies — exactly who this project targets |
| Instaply & job_search_automation harvested, not revived | the valuable part of each is small; running either as a service duplicates infrastructure |
| Paraform integration deferred | explicit user call — Ashby/Greenhouse stay the only discovery sources for now |
| No sector filter on QUALIFY | job_search_automation's company list was never a real filter to preserve |
| Recruiters are a first-class contact target | likeliest responder after the hiring manager; the old ban confused the channel with the person |
| Replies are tracked per contact role | "which contacts respond" was unanswerable, so targeting arguments could never be settled |

## Commands

```bash
python3 qualify_run.py --days 7 --limit 25          # score recent matches
python3 qualify_run.py --days 3 --detail            # with per-dimension breakdown
python3 qualify_run.py --days 7 --min-score 65 --json
```

Reads the live tracker DB on the Pi read-only over SSH, writes nothing, and
calls no LLM. Requires the Pi to be reachable (see the tracker's `PI.md`).

The full pipeline (QUALIFY through a Gmail draft) is driven by the
`outreach` skill — ask Claude Code to "run outreach" rather than invoking
these directly. Under the hood it's `outreach_run.py`:

```bash
python3 outreach_run.py prepare --days 1 --json     # who's worth a contact call
python3 outreach_run.py status                      # pending drafts / contacted companies
python3 outreach_run.py finalize                    # verify + Gmail draft + claim (JSON on stdin)
```

This writes to local `outreach.db`, not `tracker.db` — see
`docs/decisions.md`.

Reviewing what is already drafted is a local web app rather than a CLI
flag, because the store's view of a draft goes stale the moment a human
touches Gmail:

```bash
python3 outreach_ui.py            # http://127.0.0.1:8765
python3 outreach_run.py discard <company>   # same thing, one company, no UI
python3 outreach_run.py replies             # did anyone write back?
```

It shows each claim next to what Gmail actually says about it and offers
the three actions that resolve a disagreement: edit the draft, discard it,
or record that it went out. **It has no send endpoint** — see the
invariants above. Bound to 127.0.0.1 only, since it can delete mail and
rewrite the dedup store. `com.yash.outreach-ui.plist` keeps it running at
login so the URL is bookmarkable.

## Rules for modifying the system

- Never let an agent or a code path skip the human-approval gate (stage 6),
  for any reason, no matter how confident stage 2/3/4 were.
- Don't add precision/exclude filters on top of QUALIFY or anywhere else in
  this pipeline — matches ashby-ny-tracker's established stance (see its
  `feedback-no-exclude-lists-in-filters` memory): broad gates, human eyes
  catch the rest.
- Don't turn Instaply or the Paraform pipeline back into running services.
  Harvest further pieces from them if needed; don't revive them.
- Prefer execution paths that reuse the existing Claude subscription over
  new metered APIs — but don't sacrifice real engineering depth to save
  money either. The project is funded specifically to be a genuine,
  demonstrable artifact, not the cheapest possible script (see
  ashby-ny-tracker's `project-budget-and-motivation` memory).
