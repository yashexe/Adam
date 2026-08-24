# Pipeline design

The full architecture. For quick orientation read `CLAUDE.md` first; this is
the depth behind it. Status tags used throughout: **[DECIDED]**, **[OPEN]**,
**[DEFERRED]** — if a stage or sub-point has no tag, treat it as decided.

## What got harvested, and why (design implications only)

Full per-component detail lives in `harvest/NOTES.md` — this is just the
"why the pipeline looks like this" summary.

- **job_search_automation** (a hand-run, single-target cold-email script)
  contributed the one deterministic piece worth reusing close to unchanged
  — the SMTP+attachment send code (stage 7) — plus the drafting rules and
  resume that shape Agent 2's prompt. Its manual per-company workflow is
  what this whole pipeline replaces.
- **Instaply** (a dormant but real, working job-matching app — 10,083
  postings scored, 21 real alerts, real cover letters against the actual
  resume) contributed the QUALIFY gate's scoring logic (`docs/qualify.md`)
  and the threshold-gated draft trick Agent 2 uses. Its FastAPI app, UI,
  and ingestion layer were discarded — ashby-ny-tracker already owns
  discovery, running a second one would duplicate it for no reason.
- **The Paraform pipeline** (a scrape-and-enrich system found alongside
  interview-prep material, richer than either of the above on
  interview-process detail) is harvested but **[DEFERRED]** — not wired
  into anything. Ashby/Greenhouse stay the only discovery sources.

## The core architectural rule

Deterministic code for anything with a lasting real-world consequence.
Probabilistic (LLM/agentic) work for anything a human doesn't want to spend
their own time on, where getting it wrong just costs a retry, not a real
mistake. This matches how production AI-agent companies in high-consequence
domains actually build this kind of system — an LLM owns the unpredictable,
conversational/creative surface; a deterministic, typed, audited layer owns
any action that touches the real world. The LLM calls tools; it doesn't get
to decide on its own.

## The eight stages

```
[1] TRIGGER — deterministic
    A fresh, role-relevant match from ashby-ny-tracker's poll cycle.
    Already exists; this project only reads it.

[2] QUALIFY — deterministic
    The harvested Instaply scoring gate — a strong-role bar, not just the
    role-shaped bar the tracker's own alert filter already applies. Full
    contract: docs/qualify.md. Profile re-parsed from the current resume
    (qualify/profile.py) — no longer stale.
    [DECIDED]: no sector filter layered on top of the score.
    [DECIDED]: independent of applying — never gated behind "I applied."
    [DECIDED]: dedup happens here, keyed on company alone — see
    "Rate-limiting policy" below.

[3] FIND THE CONTACT — agentic (Agent 1)
    Full contract: docs/agents.md. [DECIDED]: agentic, not a pure API call
    — see docs/decisions.md for the Hunter/Apollo coverage research behind
    that call.

[4] VERIFY THE EMAIL — deterministic [DECIDED, implemented]
    Resolve the candidate address from the domain's own pattern and check
    it against Hunter.io (outreach/verify.py), live. Produces a confidence
    label, cached per address. Agent 2 gets only the label and the address
    — never raw lookup data it doesn't need. Mirrors a pattern real
    production agent systems use: restrict what reaches the LLM's context,
    don't just gate its output.

[5] DRAFT THE EMAIL — agentic (Agent 2)
    Full contract: docs/agents.md.

[6+7] REVIEW AND SEND — Gmail Drafts [DECIDED 2026-08-23]
    The draft is appended to the Gmail Drafts folder over IMAP
    (outreach/gmail_draft.py), with the resume attached and the signature
    applied. Review, editing, and sending all happen in Gmail.

    This replaced both a purpose-built review surface and the SMTP send
    step. It is a stronger form of the approval invariant than either: no
    code path in this project can transmit a message at all — the IMAP
    connection only appends to a mailbox — so reaching a recipient
    strictly requires a person pressing Send in a mail client.

    Uses the existing Gmail app password, which IMAP accepts. The Gmail
    API was not used: it needs OAuth and a Cloud project to do something
    IMAP APPEND does with credentials already on hand.

    Consequence: a draft addressed to an unverified address now sits one
    click from sending. Stage 4 matters more under this design, not less.

[8] LOG — deterministic
    Dedup key is the company alone. See "Rate-limiting policy" below.
```

## Rate-limiting policy [DECIDED]

At most one outreach attempt per company, full stop, regardless of how many
separate qualifying postings that company produces afterward.

- A company becomes "claimed" the moment a draft is created for it.
- A second qualifying posting while the first draft is still pending: the
  better posting is **recorded as a note**, not swapped in — no second
  draft, no second contact lookup, and no change to what the claim points
  at. [REVISED 2026-08-23: this originally said the draft was repointed at
  the better posting. That predates drafts living in Gmail. Repointing
  updates the database row but not the email — which was written against
  the original posting and may have been edited by hand since — leaving the
  record describing a draft that does not exist. It also treats the QUALIFY
  score as a proxy for "better", which the gate has not earned: it swapped
  company-a's claim from "Software Engineer" (90) to "Software Engineering
  Intern" (95).]
- A second qualifying posting after the first was already sent: logs as
  "company already contacted," no new draft, no repeat notification.
- Re-contacting a company later is a manual, deliberate action, never
  something the pipeline does on its own.

## Data model [DECIDED: lives in `tracker.db` directly, no separate DB]

Same SQLite file, same `connect()`/schema pattern ashby-ny-tracker already
has; sending reuses the existing SMTP setup from `notify.py`/`.env`.

```
pending_outreach
    company_slug, platform, job_id       -- the posting currently referenced;
                                             overwritten if a better posting
                                             arrives while pending
    contact_name, contact_role, contact_email, confidence
    status                               -- drafted / sent / rejected
    created_at, updated_at
    -- draft_subject/draft_body dropped: the draft body lives in Gmail once
    -- stage 6 appends it, and a second copy here would only drift from
    -- whatever the human actually edited before sending.

outreach_log
    company_slug, contact_email, outcome, sent_at
    -- PRIMARY KEY is company_slug alone: dedup is per-company.
```

Same spirit as `pending_alerts`/`seen_jobs` in ashby-ny-tracker: nothing is
lost on a failed step, nothing gets contacted twice.

## Execution model [DECIDED, implemented 2026-08-23]

`poll.py` today is pure Python, zero LLM calls, running unattended every 10
minutes via cron on the Pi. Stages 3 and 5 need an actual agent — that
can't live in that loop as-is, so this needed a different runtime.

**Resolved as Claude Code-orchestrated**, not a standalone script with its
own metered API key: the `outreach` skill (`.claude/skills/outreach/SKILL.md`)
drives the deterministic `outreach_run.py` CLI and invokes the
`contact-finder` and `drafter` subagents directly. This follows from the
project's funding context (see ashby-ny-tracker's
`project-budget-and-motivation` memory): it reuses a subscription already
being paid for instead of opening a new billing surface, and is a more
demonstrable artifact than an ad-hoc script. Not chosen for cost-minimization
alone — the standard is genuine engineering depth worth showing in an
interview, and this happens to also be the cheaper path, not the other way
around.

**Human-triggered, not scheduled.** The skill runs when asked ("run
outreach" in a Claude Code session), not on a cron-style unattended
schedule the way `poll.py` is. Whether it's ever worth automating that
trigger is a real but low-urgency open question — nothing about the
pipeline design blocks on it, since stage 6 requires a human in the loop
regardless of what triggers stage 3.
