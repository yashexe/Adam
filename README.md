# Adam

> *The Creation of Adam* — two hands reaching across a small gap.

The gap this closes is the one between a company realising it needs
someone and the right person hearing from me directly. ashby-ny-tracker
finds the posting within minutes; Adam reaches the human who can act on it,
before the queue forms.

An agentic pipeline that turns a fresh job match from ashby-ny-tracker (an
upstream project that polls job boards and finds NY postings) into a
drafted, personalized cold-outreach email, ready for a human to send. All
eight stages are implemented and have run end to end
against live data — invoke the `outreach` skill, or run `outreach_run.py`
directly. See `docs/status.md` for the per-component breakdown.

Start here:

- **`CLAUDE.md`** — dense project context, read this first.
- **`PIPELINE.md`** — the full 8-stage architecture and design reasoning.
- **`docs/`** — subsystem contracts (`agents.md`, `qualify.md`), the
  decision log (`decisions.md`), and current implementation state
  (`status.md`).
- **`harvest/NOTES.md`** — where every piece of reused code came from
  (job_search_automation, Instaply, a Paraform scrape pipeline) and its
  status.
