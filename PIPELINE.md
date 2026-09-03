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

[2] QUALIFY — deterministic eligibility + LLM judge
    Hard eligibility rules (facts: employment type, title, stated years,
    citizenship) run as deterministic code; the fit score is the
    relevance-judge's 0-100 directly, batched and cached per posting,
    with frozen anchor postings in every batch to catch drift. The
    Instaply-harvested deterministic composite that used to wrap the
    judge was deleted 2026-08-26 after measurement showed it only
    subtracted — full contract and the evidence: docs/qualify.md.
    [DECIDED]: no sector filter layered on top of the score.
    [DECIDED]: independent of applying — never gated behind "I applied."
    [DECIDED]: dedup happens here, keyed on company alone — see
    "Rate-limiting policy" below.

[3] FIND THE CONTACT — agentic (Agent 1)
    Full contract: docs/agents.md. [DECIDED]: agentic, not a pure API call
    — see docs/decisions.md for the Hunter/Apollo coverage research behind
    that call, and docs/research/contact-strategy-findings.md for the
    2026-08-26 verification that no public substrate (ATS APIs, logged-out
    LinkedIn, data vendors) can replace it for this population.
    [REVISED 2026-08-26]: returns a ranked slate of up to three candidates
    with per-candidate evidence, not one committed pick — the selection
    itself became the human-reviewable decision the old design hid.

[4] VERIFY THE EMAIL — deterministic [DECIDED, implemented]
    Two passes since 2026-08-26. First, `verify-slate` resolves an address
    for every slate candidate against the domain's own pattern and
    Hunter's per-address roster (one cached domain-search for the slate,
    verification credits spent only until the first deliverable
    candidate), so the human picks with reachability in view instead of
    finding out after the drafting spend. Then finalize re-resolves and
    verifies whichever candidate was chosen — the advisory pass never
    binds (outreach/verify.py). Produces a confidence label, cached per
    address. The mailbox check itself is a provider chain: a direct SMTP
    RCPT probe from this Mac first — keyless and free; since 2026-09-02
    it settles about half of all addresses outright — then free-tier
    vendors as their keys exist, Hunter last. A catch-all verdict from a
    probe is provisional: Hunter, whose verdict draws on sources and
    bounce history rather than a live probe alone, gets a turn to sharpen
    it. An inconclusive answer passes to the next provider and is never
    cached. Resolution itself has a keyless rung too (2026-09-02): when
    Hunter has no roster for the domain, the conventional patterns for
    the name are rendered and the domain's own server is asked which one
    exists. A full-name hit keeps the probe's label; a partial-name hit
    ({first}@, the small-company norm) is labeled risky with the namesake
    caveat spelled out. Catch-all domains defeat every keyless route and
    still need Hunter's roster. Agent 2 gets only the label and the address — never raw
    lookup data it doesn't need, with one deliberate window: Agent 1's
    `personalization_context`, a digest of public company facts the draft
    builds its bridge and its "something about them" line from (widened
    from one fact to a digest 2026-08-28, see docs/decisions.md), which
    carries what the company said, not how the contact was found. Mirrors a pattern real production agent
    systems use: restrict what reaches the LLM's context, don't just gate
    its output.

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

## Rate-limiting policy [DECIDED; touch count revised 2026-08-26]

One contact per company, at most two touches to that contact, regardless
of how many separate qualifying postings the company produces afterward.
The second touch is the one permitted follow-up bump: a two-sentence reply
in the original Gmail thread, offered only after 5–15 business days of
*confirmed* silence (`bumps` checks Gmail live first — a reply or a bounce
disqualifies), drafted by Agent 2 in bump mode, sent by a human like
everything else, and recorded in `outreach_log.follow_up_at` so the store
itself refuses a second one. This amends the original "one attempt, full
stop": the single-send rule was the one part of the policy that measured
evidence actually contradicted (follow-up lift is the most replicated
finding in the outreach literature, and candidate follow-ups are expected
per HR-manager survey data — docs/decisions.md, "One follow-up bump").
What did not change: one company, one person, one thread, ever.

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

`poll.py` today is pure Python, zero LLM calls, running unattended every 5
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

**Unattended since 2026-09-03, with the same two human gates.** Until
then the skill ran only when asked, which threw away the head start the
tracker wins. Now two loops run on this Mac (`outreach/unattended.py`,
`bin/tick.py`, `com.yash.adam-tick.plist`, installed by hand):

- **Tick, every five minutes, deterministic.** One read-only SSH query for
  postings newer than a watermark, the four hard rules, the ignore list,
  the store's claims; survivors go to a queue. It fires when the oldest
  has waited 15 minutes or five have piled up, which is what keeps the
  judge's fixed per-call cost from being paid once per posting.
- **Run, when the tick fires, one headless Claude session** of the
  `outreach` skill in unattended mode, restricted to the tools it needs
  and a 25-minute wall clock. Judge the window, prepare, and for each new
  company within budget (three per run, eight per day, counted in the
  store): research and resolve the slate; at 70 and up draft to rank one
  when its address is clean and park the slate otherwise; at 65–69 park
  the slate; at 58–64 list it as borderline. Never a draft to a `risky`
  address without a human. Then the read-only reply check and a summary.

The pick for a parked slate is a button in the review UI; the next run
drafts it. Sending is Gmail. A failed run requeues its postings once.
Companies in process through other channels are closed in the store by
hand so the run cannot email them.
