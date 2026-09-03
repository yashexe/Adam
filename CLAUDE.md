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
[2] Qualify    det.+AGENT  hard eligibility rules + the relevance-judge's
                           score (which IS the score) — docs/qualify.md
[3] Find contact  AGENT  Agent 1, ranked slate of up to 3 — docs/agents.md
[3.5] Pick     det./human  verify-slate resolves reachability; human picks
[4] Verify     det.      mailbox check: keyless SMTP probe first, free-tier
                        vendors, Hunter last — outreach/verify.py
[5] Draft      AGENT     Agent 2 — docs/agents.md
[6] Approve    human    review/edit in Gmail — the one gate that can't be
                        automated away
[7] Send       human    a person presses Send in Gmail; the repo has no
                        send path — drafts arrive via IMAP APPEND only
                        (outreach/gmail_draft.py, DECIDED 2026-08-23)
[8] Log        det.      company-level dedup; one follow-up bump per
                          company, human-sent (outreach_run.py bumps/bump)
```

Who Yash is, and every story a draft can draw on: `PROFILE.md` — the single
place his background is maintained. Agent 2 reads it for every draft, so
changing a fact there changes all future drafts. Never paste résumé facts
into a prompt.

Full depth: `PIPELINE.md`. Subsystem contracts: `docs/agents.md` (Agent 1/2),
`docs/qualify.md` (the QUALIFY gate). Where every harvested piece of code
came from: `harvest/NOTES.md`. Why each fork was resolved the way it was:
`docs/decisions.md`. What's actually built vs. designed: `docs/status.md`.
What's next, and why: `docs/roadmap.md`.

## Non-negotiable invariants

- Dedup is per-company, never per-posting or per-contact.
- One contact per company, at most two touches: the initial email plus one
  follow-up bump after 5–15 business days of confirmed silence, in the
  same thread, human-sent, recorded in `outreach_log.follow_up_at` so the
  store refuses a second. The bump (added 2026-08-26) is the only
  evidence-backed amendment the contact-strategy research survived
  verification with — see `docs/decisions.md`. Never a sequence, never a
  second thread, never a second person.
- Nothing sends without live, explicit human/chat approval — no exception,
  regardless of how confident any agent is.
- Outreach is independent of applying — never gated behind "did I apply."
- No sector filter layered on top of the QUALIFY score. The four hard
  eligibility rules in `qualify/eligibility.py` (full-time only, no
  frontend-titled roles, no stated minimum above his years, no stated
  visa-sponsorship refusal / citizenship requirement — all 2026-08-25
  except the first two) are the deliberate exception and must stay tiny.
  The visa rule is deliberately narrow: only an explicit US-citizenship
  requirement disqualifies, never a plain "no sponsorship" statement — he
  may be TN-eligible and companies often say the latter without meaning
  to rule that route out.
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
  step between them. One deliberate window since 2026-08-26:
  `personalization_context`, public company facts Agent 1 hands the draft
  — what the company said, never how the contact was found. Source notes,
  evidence, and verification detail still never reach Agent 2.
- Deterministic code owns anything with lasting real-world consequence;
  agentic work owns only what's tedious-but-cheap-to-retry.
- ashby-ny-tracker's and Instaply's own ingestion/serving layers are not
  revived — this project does not duplicate discovery or stand up a
  second running service.

## Current status

All eight stages are implemented and have run end to end against live
data, orchestrated by the `outreach` Claude Code skill
(`.claude/skills/outreach/SKILL.md`) driving `outreach_run.py` plus the
`contact-finder` and `drafter` subagents. Since 2026-08-26 the QUALIFY
fit score is the batched relevance-judge's 0-100 directly — the
Instaply-inherited deterministic composite around it was measured to be
a lossy copy of the judge and deleted (item 9 below); hard eligibility
stays deterministic, and frozen anchor postings in every judge batch
catch drift. Numbers in `docs/qualify.md`. Also since 2026-08-26,
contact selection runs as a human-arbitrated slate with one follow-up
bump per company (item 10 below), after a commissioned research report
was adversarially verified and largely refuted —
`docs/research/contact-strategy-findings.md`. Full breakdown:
`docs/status.md`.

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
   strong became 72 (Instaply's 85 sat above the entire observed
   distribution — max composite is 83), and the 65 spend bar was kept
   deliberately after measuring it, no longer inherited. Full derivation:
   `docs/qualify.md`, "Where the cutoffs come from". Re-derived again
   2026-08-26 after the scorer rebalance (item 8): now 69/64.
4. **Execution model** [DECIDED] — Claude Code-orchestrated on this Mac,
   human-triggered via the `outreach` skill, reusing the existing
   subscription rather than a new metered API. Built and running, not just
   leaned toward — see `PIPELINE.md`'s Execution model section. Whether it
   should ever move to an unattended/scheduled trigger instead of
   chat-triggered is still open, but not urgent. Note the Pi cannot host
   it either way: armv7l 32-bit, Node v10, no `claude` binary.
5. **`git init`** [DECIDED] — done. Pushed to `github.com/yashexe/Adam`.
6. **The judge never ran in a live outreach call** [FIXED 2026-08-25] —
   `SKILL.md` went straight from `prepare` to Agent 1; `judge`/`judge-save`
   only ever ran because they were invoked by hand during the tuning work
   in `docs/qualify.md`. Caught live: company-aa's Technical Account
   Manager posting scored 89 with semantic_fit (weight 30, the dimension
   that tells relationship-management from engineering) silently absent
   from the denominator — a same-titled posting judged properly elsewhere
   scored 12. Fix: `SKILL.md` Step 1 now runs `judge` on the window and
   saves the result before calling `prepare`, every time.
7. **Visa sponsorship was a soft 2-point signal, not a hard fact**
   [FIXED 2026-08-25] — hand-reviewing the freshly-judged top scorers
   turned up company-e (92, Founding Engineer) stating "US citizenship
   required; no visa sponsorship available." Yash: "if they ask US
   citizenship theres no point moving forward" — but narrower than "no
   sponsorship": only an explicit citizenship requirement disqualifies,
   never plain "no sponsorship" alone, since he may be TN-eligible and
   companies often say the latter without meaning to rule that route out.
   `qualify/extractor.py` gained a distinct `citizenship_required` signal
   (separate from the existing `visa_sponsorship` field, which stays a
   neutral signal); `qualify/eligibility.py` gained a fourth hard rule,
   `check_citizenship_required`; `NEEDS_VISA_SPONSORSHIP` in
   `qualify/profile.py` stays `False` on purpose; `.claude/agents/drafter.md`
   gained a ban on ever mentioning visa/sponsorship/citizenship in a
   draft; a wiring bug that left the old soft `preferences_fit` visa
   sub-score permanently inert (reading a `job_data` key nothing ever
   set) was fixed along the way. Full derivation: `docs/qualify.md`,
   "Visa-sponsorship hard eligibility".
8. **~20 of the deterministic 70 points were dead weight** [FIXED
   2026-08-26] — hand-tracing a recruiter-pulled FDE posting exposed four
   compounding problems: the keyword matcher treated `-` as a boundary
   ("go-live" matched the `Go` language, and that lone false positive
   fired the skills gate, capping a ~68 posting at 49); the gate had no
   minimum-evidence floor; `experience_fit` had become a constant once
   `check_years` landed; and `preferences_fit` had 10 of 15 raw points
   unconditionally free under the current profile. Fix: hyphen handling
   made keyword-specific (`HYPHEN_SENSITIVE_KEYWORDS`), required-skills
   samples under 3 extracted keywords now score UNKNOWN instead of noise
   (`REQUIRED_SKILLS_MIN_EVIDENCE`), `experience_fit` deleted,
   `preferences_fit` cut to 5, and the freed 20 points moved to
   role_title_fit and required_skills_fit (25/25). Validated offline
   against 305 cached judged postings: judge<50 leakage at the spend bar
   halved, zero judge≥70 postings below it, tiers re-derived to 69/64.
   Full derivation: `docs/qualify.md`, "Dead weight in the deterministic
   composite".
9. **The composite itself was the problem** [REDESIGNED 2026-08-26] —
   item 8's rebalance was the third composite fix in three days, and
   measuring whether the deterministic 70 points earned their keep
   answered no: every deviation they produced against the judge was an
   error in the same direction (20 bad promotions, 1 borderline demotion,
   0 catches), the composite correlated +0.89 with the judge it wrapped,
   ground truth ordered 7/7 on the judge alone, and judge test-retest
   stability measured +0.97. `qualify/scorer.py` deleted; the
   relevance-judge's 0-100 IS the QUALIFY score; the judge reads 3000
   chars, returns a shape/seniority/domain rubric, and four frozen
   anchor postings in every batch turn drift into a warning instead of a
   silent reordering. Eligibility, extraction-as-metadata, and dedup stay
   deterministic. Corpus re-judged (303 postings), tiers on the judge's
   scale: strong ≥70, spend bar 65. Full derivation and the risk taken:
   `docs/qualify.md`, "The judge becomes the score".
10. **Contact selection redesigned on verified research** [DONE
   2026-08-26] — a deep-research report on contact strategy was
   commissioned (`docs/research/contact-strategy-brief.md`) and its
   claims adversarially verified before ingestion: seven parallel
   verification agents against primary sources, plus this repo's own 625
   cached board responses. Most of the report failed verification — its
   #1 recommendation (drop the résumé PDF, new sending domain,
   apply-first) inverted under primary sources, and its ATS-API and
   LinkedIn contact substrates turned out fabricated or auth-walled —
   which vindicated Agent 1's agentic approach as what the data landscape
   actually permits. What survived became the redesign: Agent 1 returns a
   ranked slate of up to 3 candidates (the trust fix — selection is now a
   human-arbitrated decision; `verify-slate` resolves reachability first,
   and the slate is stored on the claim and rendered in the review UI);
   one follow-up bump per company (the yield fix — the only policy
   amendment, see the invariants); `personalization_context` flows from
   Agent 1's research to the drafter as the one deliberate window in the
   research/draft wall; and Hunter's roster now backs both slate
   resolution and `resolve_address`, with the domain-search cached per
   domain. Full verdicts: `docs/research/contact-strategy-findings.md`.

11. **The drafts converged on a template** [FIXED 2026-08-28] — Yash's
   verdict on the pipeline's output was "the drafts themselves suck", and
   reading the three real pipeline emails side by side confirmed it: one
   email with the slots refilled (identical congrats opener, identity
   sentence, and closer; 8 of 9 stored subjects the same shape). Two
   structural causes: the drafter was starved (Agent 1's research was
   discarded except one `personalization_context` fact) and `drafter.md`
   had grown into ~530 lines of bans around a single worked example, so
   the model collapsed onto paraphrasing the example. Fix:
   `personalization_context` widened to a four-to-eight-bullet company
   digest (same wall, more material); `drafter.md` rewritten around
   finding the **bridge** (the one sentence naming the true overlap
   between their problem and his work) with the single model email
   removed, an anti-template rule, per-tier sentence budgets, and the
   mechanical density rules delegated to `outreach/draft_lint.py`; the
   drafter moved from Sonnet to Opus. The redraft run also caught a live
   citizenship-eligibility leak ("Must be a U.S. citizen" defeats a
   plain-"us" substring; `qualify/extractor.py` now normalizes the
   abbreviation) and a stale claim (company-j's "deleted" draft had in fact
   been hand-sent; the prior-contact check refused the re-draft and the
   send is now recorded). Full derivation: `docs/decisions.md` and
   `docs/agents.md`, "Agent 2 — the template collapse".

12. **Hunter's quota death blocked all drafting** [FIXED 2026-09-02] —
   every Hunter counter on the free plan hit zero on 2026-08-28 and again
   before the 09-11 reset, and with no roster a named contact at a company
   with no observed personal address was unresolvable: three researched
   companies produced zero drafts on 2026-09-01. Two fixes, both keyless.
   Mailbox verification now walks a provider chain fronted by a direct
   SMTP probe from this Mac (`_verify_via_smtp`), benchmarked against all
   40 addresses Hunter had labeled (21 identical, 16 catch-all from here
   where Hunter's data still wins and gets the turn, 3 skips, 1
   contradiction). And address resolution gained a keyless rung
   (`probe_patterns`): render the conventional patterns for the name, ask
   the domain's own server which exists. Full-name hits keep the probe's
   label; partial-name hits (`{first}@`, the convention at 12 of 18 cached
   rosters) are labeled `risky` with the namesake caveat spelled out, so
   the review card says "confirm the person" rather than "verified".
   Benchmarked against 46 roster-attributed people: 14 exact, 7 full-name
   aliases, 0 wrong-person, 21 on catch-all domains where nothing keyless
   can work. Catch-all domains still need Hunter's roster; that is the
   residual dependency. Derivation: `docs/decisions.md`.

13. **The pipeline only ran when asked** [FIXED 2026-09-03] — every stage
   was built, resolution had just gone keyless, and the whole thing still
   slept until someone typed "run outreach", which threw away the head
   start the tracker exists to win. Now a deterministic tick every five
   minutes (matching the Pi's poll) queues new eligible postings behind a
   watermark and fires one headless run when the oldest has waited 15
   minutes or five have piled up. The run drafts to rank one on a clean
   resolve for scores ≥ 70, parks the slate for a human pick otherwise
   (and always for 65–69), lists 58–64 as borderline, and never drafts to
   a `risky` address. Budgets, retries, and an ignore list for slugs that
   are not companies (a recruiting marketplace with 288 postings) live in
   `outreach/unattended.py` and the store, not in the prompt. Both human
   gates are untouched: picking is a button in the review UI, sending is
   Gmail. Derivation: `docs/decisions.md`.

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
| Paraform integration deferred | explicit user call; discovery itself is the tracker's decision, and since 2026-08-29 it also feeds Lever and Workable (`qualify/boards.py` fetches all four) |
| No sector filter on QUALIFY | job_search_automation's company list was never a real filter to preserve |
| Recruiters are a first-class contact target | likeliest responder after the hiring manager; the old ban confused the channel with the person |
| Replies are tracked per contact role | "which contacts respond" was unanswerable, so targeting arguments could never be settled |
| The judge's 0-100 is the QUALIFY score | the deterministic composite was measured to be a lossy copy of the judge that only subtracted; deleted, not down-weighted |
| Agent 1 returns a slate, the human picks | a committed single pick hid the one decision that most needed review; the slate makes selection arbitrable at zero marginal human time |
| One follow-up bump, never a sequence | follow-up lift is the most replicated finding in the outreach literature; sequences import bulk-sales cadence into one-to-one candidate mail |
| Personal Gmail and the attached résumé stay | the research report's deliverability case collapsed under primary sources — a fresh domain is the documented spam signal, and an unsolicited cloud link is the documented phishing pattern (docs/research/contact-strategy-findings.md) |

## Commands

```bash
python3 qualify_run.py --days 7 --limit 25          # rank recent matches by judge score
python3 qualify_run.py --days 3 --detail            # with the judge's rubric and reason
python3 qualify_run.py --days 7 --min-score 65 --json
```

Reads the live tracker DB on the Pi read-only over SSH, writes nothing, and
calls no LLM — judge scores come from the cache the `outreach` skill fills;
unjudged postings are listed unranked. Requires the Pi to be reachable (see
the tracker's `PI.md`).

The full pipeline (QUALIFY through a Gmail draft) is driven by the
`outreach` skill — ask Claude Code to "run outreach" rather than invoking
these directly. Under the hood it's `outreach_run.py`:

```bash
python3 outreach_run.py prepare --days 1 --json     # who's worth a contact call
python3 outreach_run.py verify-slate                # which slate candidates are reachable (JSON stdin)
python3 outreach_run.py status                      # pending drafts / contacted companies
python3 outreach_run.py finalize                    # verify + Gmail draft + claim (JSON on stdin)
python3 outreach_run.py bumps                       # who's due their one follow-up
python3 outreach_run.py bump <company>              # draft it, in-thread (body on stdin)
python3 outreach_run.py slates                      # researched companies awaiting your pick
python3 outreach_run.py slate approve <company> "<name>"   # the next unattended run drafts it
python3 outreach_run.py ignore <slug> "<reason>"    # not a company (marketplace, agency)
python3 outreach_run.py tick                        # the 5-minute unattended check: fire|idle
```

Unattended, since 2026-09-03: `bin/tick.py` (run by
`com.yash.adam-tick.plist` every five minutes, installed by hand) asks the
Pi for new postings and, when enough have waited, launches one bounded
headless run of the `outreach` skill — judge, research, resolve, draft to
rank one when the address is clean, park the slate otherwise. Three
companies per run, eight per day, counted in code. Drafts land in Gmail
exactly as before; the pick for a parked slate is a button in the review
UI. `docs/decisions.md`, "The pipeline runs itself".

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
