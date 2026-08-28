# Agent contracts

Two agentic stages in the pipeline (see `PIPELINE.md` for where they sit).
Contracts, not prompts — the actual prompt text gets written at
implementation time, informed by the drafting rules and reference examples
already sitting in `harvest/`.

## Agent 1 — Contact Finder

| | |
|---|---|
| **Purpose** | Find the people worth contacting at a company that just cleared the QUALIFY gate — a ranked slate of up to three, for a human to pick from |
| **Trigger** | A qualifying match, after QUALIFY, before verify (stage 3) |
| **Input** | Company name/slug, job posting context (title, department, funding_hint, location) |
| **Output** | `{ company, domain, candidates: [{name, role, confidence, evidence}], observed_address, observed_address_source, personalization_context, source_notes }` — ranked, empty `candidates` = skip the company |
| **Tools** | Web search / page fetch (company team/about page, funding announcements, GitHub, public search results) |
| **State** | Stateless — one call, one company, no memory between runs |
| **Allowed actions** | Read-only research only. Never writes persuasive copy, never judges whether the *role* is a good fit (QUALIFY already decided that) |
| **Human-approval boundary** | None needed here — this stage only produces a candidate, verified deterministically next (stage 4) before anything reaches a human |
| **Failure behavior** | No confident result → the match doesn't proceed to Agent 2; logged, not retried automatically, never blocks the rest of the pipeline |
| **Expected cost** | One LLM call (with tool use) per qualifying company. This is the reason a company must be "claimed" on first attempt (see decisions.md, dedup) — retrying blindly on every new posting would multiply cost for no benefit |

*Why this can't just be an API call:* see `docs/decisions.md` — Hunter/Apollo
coverage collapses on small, freshly-funded companies, which is exactly the
population this project cares about reaching first.

## Agent 1 — measured behavior (spike, 2026-08-23)

Implemented as a Claude Code subagent definition
(`.claude/agents/contact-finder.md`, Sonnet + WebSearch/WebFetch) and run
against five small NY companies from live QUALIFY output. No metered API
key involved.

**Hit rate: 5/5 named a real, checkable person** — a CTO, a VP Engineering,
an SVP Engineering, a Head of Engineering, and a co-founder/CTO. Zero
`info@` fallbacks, zero empty results. The premise the project rests on —
that agentic search beats the API coverage cliff on sub-50-person companies
— holds on this sample.

Quality was uneven below that headline. One result reached `high`
confidence (pattern derived from a named address on the company's own press
page). Three rested on genuinely primary evidence: a GitHub commit-metadata
address, a company blog byline, a co-founder's address in a public review
reply. One derived its pattern from RocketReach — an aggregator publishing
its own inferred guess — and still reported `medium`.

Two prompt defects the spike exposed, both since fixed in the definition:

- **Three of five falsely reported the triggering posting as filled or
  pulled.** All five were live. The agents were re-verifying a posting the
  pipeline had already fetched, and mistook search pagination for absence.
  The posting is now stated to be authoritative input, not a research
  target — which removes the false claims and the wasted calls at once.
- **Aggregator-sourced patterns were being reported as evidence.** Now
  explicitly capped at `low` confidence and barred from `pattern_evidence`.

**The high-confidence result was the wrong address.** Verified against
Hunter the same day: `constructed.guess@company-a.com` scored 0, `smtp_check:
false`, on a domain that is not catch-all — a real negative, not an
inconclusive one. Agent 1 had followed its instructions exactly, inferring
`first.last` from `press.contact@company-a.com` on company-a's own press
page. That source address is genuine and verifies at 100; it is simply the
exception. company-a's convention is `{first}`, and `correct.pattern@company-a.com` verifies
at 100.

The lesson is structural, not a prompt tweak: one real address is a sample
of size one, and Hunter's domain-search returns the whole domain's pattern
for the same single credit. Pattern inference moved out of Agent 1 and into
stage 4 (`outreach.verify.resolve_address`). Agent 1 now reports the person
and any address it observed, and is explicitly told not to construct the
target's address. Its confidence rating now describes the person only.

This is the clearest vindication so far of the deterministic-boundary
design in `PIPELINE.md`: the agent was confident, sincere, well-sourced,
and wrong, and a cheap deterministic check downstream caught it before a
human could act on it.

### Full verification sweep, all five spike contacts (same day)

Every address Agent 1 produced, checked against Hunter, with its
domain-wide pattern compared to what the agent inferred:

| Company | Agent 1's address | Verdict | Hunter pattern | Agreed |
|---|---|---|---|---|
| company-a | `constructed.guess@` | **invalid** (0) | `{first}` | no |
| company-e | `[redacted]@` | verified (89) | *no data* | n/a |
| company-b | `[redacted]@` | verified (92) | `{first}` | yes |
| company-f | `[redacted]@` | verified (100) | `{first}.{last}` | yes |
| company-g | `[redacted]@` | **invalid** (0) | `{first}.{last}` | no |

**Agent 1 alone: 3/5. With stage-4 pattern resolution: 5/5.** Both failures
were the same mistake — generalizing a domain convention from one genuine
but unrepresentative address. company-a's came from a press page, company-g's from a 2022 blog byline whose own agent explicitly noted it was a
single data point with no corroboration. Both were caught and corrected by
`resolve_address`, which reads the whole domain for one credit.

Two things worth carrying forward:

- **Agent 1's confidence did not predict correctness, and was mildly
  inverted on this sample.** The one `high` result was wrong. company-b,
  whose pattern came from RocketReach — the weakest evidence in the batch,
  and the reason the aggregator ban was added — was right. Confidence about
  a *person* is not confidence about an *address*, which is precisely why
  the two were separated.
- **Hunter had no addresses at all for `company-e.com`.** There, Agent 1 was
  the only source, and its answer verified at 89. The agent is not redundant
  with the API; they cover different failures. That said, Hunter did return
  a usable pattern for 4 of 5 domains, which is better coverage than the
  research in `docs/decisions.md` predicted for companies this size.

**Cost per company:** 52k–79k tokens (median ~72k), 13–31 tool calls,
75–320 seconds. On the subscription this is free; the same work on a
metered Sonnet key would run roughly $0.20–0.30 per company. Either way it
confirms the dedup design in `docs/decisions.md`: this is far too expensive
to re-run per posting rather than per company.

## Agent 1 — the slate (2026-08-26)

The single-pick contract above was revised after the contact-strategy
research pass (`docs/research/contact-strategy-findings.md`): Agent 1 now
returns a **ranked slate of up to three candidates**, each with its own
evidence and confidence, and the human picks at review time with
reachability already resolved (`outreach_run.py verify-slate` — one cached
Hunter domain-search per company, verification credits spent only until
the first deliverable candidate).

Why: the agent's *finding* was never the weak point (5/5 real people in
the spike, and the verification pass established that no public
substrate — ATS APIs, logged-out LinkedIn, data vendors — could replace
agentic research for sub-50-person companies). The weak point was the
invisible *commitment*: a wrong ladder call surfaced only after the
drafting spend, as a discard, and a dead mailbox surfaced only after all
the research was already burned (company-h and company-i, 2026-08-25). The slate
turns both failures into a ten-second human choice among alternatives
that are already on screen.

Two additions ride along. `personalization_context` carries public
company facts (funding, launch, blog post, what they build and for whom)
to the drafter — the one deliberate window in the research/draft wall,
carrying what the company said publicly, never how the contact was
found. Originally one or two facts; widened 2026-08-28 to a four-to-
eight-bullet digest after the drafts converged on a template (see "Agent
2 — the template collapse" below). And the ladder
gained a clarification, not a reordering: at sub-30-person companies the
founder/CTO *is* the rung-1 hiring manager, because first recruiter hires
arrive around 40–50 employees.

## Agent 2 — the voice rewrite (2026-08-23)

The first drafts were competent and did not sound like Yash. The rewrite is
grounded in his actual sent mail (43 messages with the résumé attached in
`[Gmail]/Sent Mail`, of which roughly a dozen are genuine cold outreach)
rather than in a general notion of good writing, plus the Finaptive work
detail in `~/Code/job-search-help/Yash_Bhavsar_Client_Interactions.pdf` and his
own written answers to Paraform screening questions.

What the real emails show:

- Short declarative sentences, contractions throughout, open enthusiasm
  ("super interested", "Love the idea of"), one exclamation mark in the
  opener or closer, parentheses for concrete examples, real system names.
- He calls the problem "messy" — messy general ledger data, raw ledgers.
- He always asks for a call, often with a proposed time. The earlier rule
  banning that was wrong for him.
- Subject lines take a `thing / who I am` shape: "Forward Deployed Engineer
  / founding engineer background".
- **No em dashes anywhere.** That was the single clearest AI tell in the
  original drafts.

Two earlier rules were wrong and were reversed. "Never propose a meeting
time" contradicted his actual practice. And "no 'not just X, I Y'" banned a
construction he genuinely writes ("I didn't just hand him software, I met
him at his technical level") — what needed banning was ornamental
parallelism, not plain contrast.

Also reversed: LLM work is no longer suppressed. The résumé-shift rule
exists to keep AI *coding tooling* off the table, not the production LLM
classification pipeline he built, which he leads with in his own emails and
which is a genuine strength.

## Agent 2 — the template collapse (2026-08-28)

The voice rewrite fixed how the drafts sounded; a second failure took
longer to see because every draft passed every rule. Yash deleted the two
pending drafts (company-j, company-k) as "the drafts themselves suck," and
reading them beside the sent company-b email showed why: three emails, one
skeleton. Identical congrats-on-funding opener, identical identity
sentence, near-identical closer, and 8 of 9 stored subjects following
"<role> / founding engineer background". Individually compliant,
collectively a mail merge.

Both causes were structural, not stylistic. The drafter wrote from the
posting plus a single `personalization_context` fact — Agent 1's 50–80k
tokens of company research were otherwise discarded — so the material a
company-specific email is made of never reached the writer. And the
prompt had accumulated ~530 lines of bans around one worked example, so
the model collapsed onto paraphrasing the example, the same
transcription failure the pre-written "In an email" lines caused in
PROFILE.md. Bans prevent badness; they cannot produce specificity.

The redesign (full entry in `docs/decisions.md`): the digest above, a
rewrite of `drafter.md` around finding the **bridge** (the one sentence
naming the true overlap between the company's problem and his work)
before writing, two real sent emails as register anchors with the single
model template removed, an explicit anti-template rule ("if a sentence
would survive pasted into an email to a different company, rewrite it"),
density rules delegated to `outreach/draft_lint.py`, and the drafter
moved from Sonnet to Opus.

## Agent 2 — Drafter

| | |
|---|---|
| **Purpose** | Write the actual outreach email body — or, in bump mode, the one permitted two-sentence follow-up (2026-08-26, see `docs/decisions.md`) |
| **Trigger** | After the human picks from the verified slate; bump mode after `bumps` confirms 5–15 business days of silence |
| **Input** | Chosen contact `{ name, role }`, posting text and metadata, the day of the week, and Agent 1's `personalization_context` when present — never its source_notes, evidence, or verification detail. Bump mode: the contact's first name, the role title, and days elapsed (never the original body — the store keeps no bodies, and a bump never restates one) |
| **Output** | Email subject + body (bump mode: body only, two sentences) |
| **Tools** | None — pure synthesis from supplied context, no search, no lookups |
| **State** | Stateless — one call, one match |
| **Allowed actions** | Draft only. Never sends (stage 7 is deterministic and human-triggered), never re-evaluates whether the contact is real (stage 4 already decided that) |
| **Human-approval boundary** | Everything this agent produces is a draft. Nothing reaches SMTP without stage 6 (explicit human approval) — this is the one boundary in the whole pipeline that cannot be automated away, and matches Claude's own hard rule against sending messages on the user's behalf without live, per-message permission |
| **Failure behavior** | A bad or empty draft just sits in `pending_outreach` unreviewed — never auto-retried, never auto-sent. Low stakes by design: nothing ships until a human reads it |
| **Expected cost** | One LLM call per verified contact, **conditionally skipped** below the qualify threshold using the harvested threshold-gating trick from Instaply's judge (`_letter_fit_threshold`): compute the fit-score needed to clear the bar before generating, so a draft is never wastefully written for a match that was never going to qualify |

*Reference material already harvested for this prompt:* the drafting rules
and tone (`harvest/from_job_search_automation/README.md`), the "resume
shift" positioning context (infra metrics over AI-tool framing), and 8 real
non-template judgments with grounded reasoning and two full letters
(a local sample-matches file, not committed) — useful calibration for
what a good, specific, non-generic draft actually looks like.
