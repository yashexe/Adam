# Roadmap — what's next

Where `docs/status.md` says what's built and `CLAUDE.md`'s "Current priority"
says what got fixed, this is what's deliberately not built yet, and why it's
next. Two items, both architectural rather than incremental — the individual
pipeline stages are solid; what's missing is how often and how automatically
they run.

## Execution model: chat-triggered → scheduled

The whole premise of this project is speed to posting — reaching a hiring
contact before the ATS queue forms (see the root `CLAUDE.md`). Right now that
promise has a gap: the `outreach` skill only runs when a human types "run
outreach" in a Claude Code session. A posting can sit qualified-but-uncontacted
for however long it takes someone to remember to open a session, which is
exactly the delay ashby-ny-tracker's 10-minute poll cycle exists to eliminate
everywhere else in this pipeline.

Closing that gap is the highest-leverage remaining change — not because any
stage is weak, but because it multiplies the value of every stage that
already works. A better-verified address or a properly-judged ranking only
pays off if outreach actually happens promptly after a posting appears.

The real constraint isn't code, it's environment. Claude Code needs to run
unattended on a schedule, which means the Mac needs to be awake and reachable
when it fires — the Pi is ruled out for this specific piece (32-bit ARM, no
`claude` binary, doesn't meet Claude Code's Node 18+ requirement). Stage 6
(human approval) doesn't change: drafts still land in Gmail unattended,
exactly as they do today, and a human still has to press Send. What changes
is only how promptly stages 1–5 run, never whether a human is in the loop for
anything that matters.

## Offload the deterministic half to the Pi

Checked directly against the code, not assumed: everything in QUALIFY's
deterministic half (`qualify/extractor.py`, `eligibility.py`,
`taxonomy.py`), board fetching (`qualify/boards.py`), Hunter verification
(`outreach/verify.py`), and the SQLite log (`outreach/store.py`) imports
nothing beyond the standard library plus `python-dotenv`. No `requests`, no
`torch` — Instaply's `sentence-transformers` dependency was rejected early in
this project for exactly this reason (dragging in a heavy dependency for one
number, see `docs/qualify.md`), and that discipline means the deterministic
half is already compatible with the Pi's Python — 3.11 since the
tracker's pyenv move (docs/tracker-upstream-2026-08-30.md corrected the
stale 3.7.3 figure here) — with essentially no porting work.

Since 2026-08-26 the fit score is the judge's alone (see `docs/qualify.md`,
"The judge becomes the score"), which narrows what the Pi could take but
not the value of taking it: `qualify/candidates.py` already runs a script
on the Pi over SSH for the NY/role filter, and extending that same pattern
would have the Pi fetch board text and apply the hard eligibility rules
every poll cycle — maintaining a standing "eligible, text in hand, ready
to judge" queue continuously, independent of whether a Claude Code session
happens to be open. The Mac's session then starts at the judge instead of
at the fetch. What can't move: the judge (an actual LLM call) and
Agent 1 / Agent 2 (need the `claude` binary) — those stay on the Mac
regardless of what else moves.

This doesn't introduce a second running service — the project's explicit
rule against reviving Instaply or Paraform as standing services (see
`CLAUDE.md`) — it's the same SSH-invoked pattern already in use, just doing
more work per invocation, plus an optional cron trigger for freshness. It's
also what actually makes the execution-model change above worth doing:
without it, a scheduled trigger just moves the fetch-and-score latency onto
a timer instead of removing it.

## Give Agent 1 a head start instead of researching blind — [DONE 2026-08-26]

Resolved by the contact-selection redesign, in a different shape than
sketched here. The problem was real — on 2026-08-25, 2 of 3 companies run
through the live skill (company-h, company-i) burned 50–80k tokens of research
and then died at the deterministic verify step — but the fix landed
downstream of Agent 1 rather than inside its prompt: Agent 1 now returns
a ranked slate of up to three candidates, and `verify-slate` resolves
every one of them against Hunter's roster and domain pattern (one cached
domain-search for the whole slate) *before* anything is drafted, so a
dead #1 means the human picks #2 instead of the company being wasted.
`resolve_address` also gained a direct roster-match fallback, which
covers the "Hunter knows this person under a local part the pattern
doesn't render" case. Handing the roster to Agent 1 up front was
deliberately not done: the roster arrives keyed by domain, which Agent 1
discovers mid-research, and the slate gets the same benefit without
putting Hunter data inside an agent prompt. Details:
`docs/research/contact-strategy-findings.md`.

## Quick-apply vs writeup classification (designed, Yash-approved)

Yash's rule (2026-08-30): if an application form has open-ended
questions, automation must not touch that application at all. The
classifier that enforces this lives here, post-judge, only for postings
above the spend bar (~10x fewer fetches than upstream placement, and the
tracker stays frozen). Greenhouse exposes form questions via its
documented public API; Ashby via the internal endpoint every visitor's
browser calls (no stability promise — must fail open); Lever/Workable
have no public form schema and stay untagged. Any long-text field, any
fetch failure, or any unrecognized shape → treated as writeup → manual.
Full design sketch with verified endpoints:
`docs/tracker-upstream-2026-08-30.md`.

## Not on this list

Paraform integration, a sector filter on top of QUALIFY, and reviving
Instaply or job_search_automation as running services are deliberately
**not** future work — each was considered and explicitly declined. See
`CLAUDE.md`'s "Decisions not to reopen" table and `docs/decisions.md` for
the reasoning behind each.
