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
deterministic scoring (`qualify/scorer.py`, `extractor.py`, `eligibility.py`,
`taxonomy.py`), board fetching (`qualify/boards.py`), Hunter verification
(`outreach/verify.py`), and the SQLite log (`outreach/store.py`) imports
nothing beyond the standard library plus `python-dotenv`. No `requests`, no
`torch` — Instaply's `sentence-transformers` dependency was rejected early in
this project for exactly this reason (dragging in a heavy dependency for one
number, see `docs/qualify.md`), and that discipline means the deterministic
half is already compatible with the Pi's existing Python 3.7.3 with
essentially no porting work.

`qualify/candidates.py` already runs a script on the Pi over SSH for the
NY/role filter — this would extend that same on-demand-remote-script pattern
to also score and gate candidates remotely, rather than shipping raw rows
back to the Mac to score locally. A Pi-side cron running this every poll
cycle would maintain a standing "cleared QUALIFY, ready to judge" queue
continuously, independent of whether a Claude Code session happens to be
open. What can't move: the semantic-fit judge (an actual LLM call) and
Agent 1 / Agent 2 (need the `claude` binary) — those stay on the Mac
regardless of what else moves.

This doesn't introduce a second running service — the project's explicit
rule against reviving Instaply or Paraform as standing services (see
`CLAUDE.md`) — it's the same SSH-invoked pattern already in use, just doing
more work per invocation, plus an optional cron trigger for freshness. It's
also what actually makes the execution-model change above worth doing:
without it, a scheduled trigger just moves the fetch-and-score latency onto
a timer instead of removing it.

## Not on this list

Paraform integration, a sector filter on top of QUALIFY, and reviving
Instaply or job_search_automation as running services are deliberately
**not** future work — each was considered and explicitly declined. See
`CLAUDE.md`'s "Decisions not to reopen" table and `docs/decisions.md` for
the reasoning behind each.
