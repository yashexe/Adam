# QUALIFY gate

Stage 2 of the pipeline. Decides whether a tracker match is worth spending
an Agent 1 call on — a *strong-role* bar, not just the *role-shaped* bar
ashby-ny-tracker's own alert filter already applies (that filter is
deliberately broad/recall-favoring; this gate is deliberately stricter,
because it gates something that costs a real agent call, not just a few
seconds of the user's reading time).

## Deterministic filters

None on top of the score below — no sector/FinTech-AR-AP filter, no
location filter beyond what ashby-ny-tracker already applied upstream. See
`docs/decisions.md`.

## Scoring — harvested from Instaply (`harvest/from_instaply/matching/scorer.py`)

A weighted composite, 0–100, computed only over dimensions that have real
data (missing data lowers confidence, not the score itself):

| Dimension | Weight | What it measures |
|---|---:|---|
| Semantic fit | 30 | LLM judgement of profile/posting fit, not embeddings — resolved, see "Semantic fit, implemented" below |
| Role/title fit | 15 | Target role family, title similarity |
| Required skills fit | 15 | Required skills found in the profile |
| Experience fit | 10 | Years, scope vs. posting's stated minimum |
| Preferences fit | 15 | Location, remote policy, salary, visa — sub-weighted 5/4/4/2 |
| Domain/company fit | 5 | Industry/domain overlap |
| Preferred-skills bonus | 10 | Nice-to-have skills present |

**Non-compensatory gate:** if required-skills coverage is under 30%, the
total score is capped at 49 regardless of how well everything else scores —
location and preference points cannot buy back a fundamental skills
mismatch. Ported unchanged; this was a calibrated, tuned rule in the
original, not something to casually adjust.

## Profile source — and the freshness requirement

The scorer needs a `structured_profile` dict (skills, roles,
years_of_experience, domains) as input. Instaply produced this via
`harvest/from_instaply/profile/parser.py` from whichever resume was current
when it last ran (pre-08/19).

**The resume was updated on 2026-08-22** (see ashby-ny-tracker's
`job_search_automation` memory / `harvest/NOTES.md`) to
`Yash_Bhavsar_Resume_08192026.pdf`. Instaply's stored profile reflected the
*old* resume and was not usable as-is. **Resolved**: the profile is now
hand-written directly from the current resume in `qualify/profile.py`
(four fields, all 46 skills resolved against `taxonomy.py`) — Instaply's
`parser.py` was deliberately not revived for a one-resume input. The gate
runs against the current profile.

## Decision tiers (re-tuned 2026-08-24)

- 72–100: strong match
- 65–71: worth a look
- <65: not strong enough to spend an Agent 1 call on

Instaply's inherited cutoffs were 85/65 — thresholds for a different
purpose (deciding whether to *email the user*) over a different score
distribution. Once semantic fit landed, 85 became unreachable: the judge's
0-100 maps onto the scorer's SEMANTIC_SIM_FLOOR..CEIL band, which
compresses the composite, and the maximum observed over the 56-posting
judged sample is 83. Nothing was ever "strong" under the old tier.

The re-tune is empirical, from the joint distribution of composite score
and judge score over that sample (see "Where the cutoffs come from"
below). `DEFAULT_MIN_SCORE` in `outreach/pipeline.py` stays 65 — no longer
inherited, now measured: below 63 the judge rates postings ≤27 almost
uniformly, so 65 already fences the noise.

## Where an LLM is involved

Two places, both from the harvested `judge.py`, both optional refinements
on top of the deterministic score:
- **The blend**: an LLM reads the actual posting + profile and produces its
  own fit score, blended 60/40 (LLM/deterministic) with the score above.
- **Semantic-fit dimension** (30 weight, the largest single dimension): the
  original computed this via a local `sentence-transformers` embedding
  model. **Resolved** as a direct LLM judgment call instead (consistent
  with "agentic handles the fuzzy work" rather than standing up a parallel
  embedding pipeline that adds `sentence-transformers`/`torch` and doesn't
  belong on the Pi) — see "Semantic fit, implemented" below.

## Measured behavior (2026-08-23, 58 live postings)

The gate is implemented (`qualify/`) and was run over 58 real postings from
a 7-day tracker window. It executes correctly. It does not currently rank
usefully — the top of the ranking was held by "Software Engineering
Intern" (95), "Senior Frontend Platform Engineer, Design Systems" (94),
"Technical Product Manager" (89), and three "Product Owner / Manager, AI
Agents" postings (85).

Per-dimension spread over the sample, which explains why:

| Dimension | Weight | Scored on | Mean | Range | Stdev |
|---|---:|---:|---:|---|---:|
| semantic_fit | 30 | 0/58 | — | — | — |
| role_title_fit | 15 | 58/58 | 11.9 | 8–15 | 1.78 |
| preferences_fit | 15 | 58/58 | 13.5 | 10–15 | 2.28 |
| required_skills_fit | 15 | 35/58 | 9.1 | 0–15 | 4.72 |
| experience_fit | 10 | 52/58 | 2.9 | 1–10 | 2.70 |
| preferred_skills_bonus | 10 | 9/58 | 4.8 | 0–10 | 3.52 |
| domain_company_fit | 5 | 58/58 | 2.3 | 1–5 | 0.85 |

Mean confidence was 55%: on the average posting, nearly half the scoring
weight had no data behind it.

**Why the ranking inverts.** Three separate effects, none of them a bug:

1. **Semantic fit was the dimension that understood the job.** At weight 30
   it was the largest, and it is the only one that reads the posting as
   prose rather than as keywords. Instaply's other dimensions were
   calibrated as adjustments *around* it. Removing it doesn't degrade the
   score gracefully — it removes the only signal that separates a Product
   Owner from a backend engineer.
2. **Two dimensions are near-constant here, and they weren't in Instaply.**
   `role_title_fit` (8–15) and `preferences_fit` (41/58 at maximum) barely
   move, because ashby-ny-tracker already filtered this population to
   NY + engineering-ish titles. They were discriminating signals over
   Instaply's unfiltered corpus; downstream of the tracker they are 30
   points of nearly free score.
3. **`required_skills_fit` saturates on thin postings.** The extractor
   treats every recognized skill in the text as required, so a posting
   mentioning one known skill the profile happens to have scores a perfect
   15/15. Five of the top ten had ≤1 recognized skill. A minimum-evidence
   guard (require ≥3) was measured and moved only one title out of the top
   ten — the problem is the missing 30-weight dimension, not this.

**Consequence.** Semantic fit is not an optional refinement that can be
deferred behind the execution-model decision, which is how it was described
above. It is load-bearing, and QUALIFY should not gate an agentic call
until it exists.

## Semantic fit, implemented (2026-08-23)

Resolved as an **LLM judgement, not embeddings** (`qualify/semantic.py`,
`.claude/agents/relevance-judge.md`). One batched call scores a whole day's
postings 0-100 on "is this a job worth contacting about", cached per
posting so re-runs are free.

`scorer.py` was not modified. The judge returns 0-100 and `to_similarity`
maps it onto the SEMANTIC_SIM_FLOOR..CEIL band the scorer expects, so
`_score_semantic_fit` computes `ratio = llm_score / 100` exactly and the
port stays verbatim. The calibration lives in `semantic.py` where it is
visible instead of being hidden in a rescale.

Measured over the same 7-day window, 56 postings judged:

| Rank | Before | After |
|---|---|---|
| 1 | Product Owner, Agentic AI (85) | **Software Engineer - Infrastructure (82)** |
| 2 | Solutions Architect (82) | **Sr. Forward Deployed Engineer (79)** |
| 3 | Developer Experience Engineer (81) | **Forward Deployed Engineer (77)** |
| 4 | Senior ML/AI Engineer (80) | Founding Engineer (74) |
| 5 | Solutions Architect, SMB Presales (78) | Business Intelligence Engineer (73) |

Companies above the bar fell from 21 to 10. Mean confidence rose from
roughly 55% to about 90%, because the largest dimension finally has data.
Every product-management, presales, engineering-management and hardware
role dropped out on its own, with no exclude list: the judge simply read
them and scored them 3-16.

The tier cutoffs became calibratable once the score measured the right
thing, and were re-tuned the next day — see below.

## Where the cutoffs come from (2026-08-24)

The full judged sample (56 postings, judgements cached in
`.cache/semantic.json`) was re-scored with semantic fit present and laid
out as composite score against judge score. The structure:

| Composite band | Postings | Judge range | What's actually in it |
|---|---:|---|---|
| 73–83 | 6 | 72–88 | backend infra, FDE ×2, founding, data eng, BI — every one a real target |
| 67–69 | 4 | 55–62 | genuine engineering roles, moderate fit |
| 63–66 | 5 | 35–55 | the gray zone — see below |
| <63 | 41 | 3–45 (nearly all ≤27) | product managers, presales, hardware, marketing |

Three facts drove the numbers:

1. **The strong cluster is clean and separated.** The six postings at
   composite ≥73 are exactly the six the judge rated ≥72, and there is an
   empty gap at 70–72 (nothing scored between 69 and 73). The strong
   cutoff is 72: it splits the gap, hugging the observed cluster floor
   (73) with a point of slack for jitter. Anything in 70–72 selects the
   same set on this sample.
2. **The composite cannot order the 63–66 zone.** company-u's IT-platform
   role (judge 40) outscored company-v's real full-stack role (judge 55)
   there — the near-constant dimensions dominate once semantic fit stops
   being decisive. No cutoff placed inside this zone means anything;
   the human reviewing the candidate list is the filter there, which
   matches the broad-gates ethos.
3. **65 as the spend bar already works.** Below 63 the judge is ≤27
   almost uniformly. Raising the bar to 67 would have excluded one
   judge-40 role (correctly) and one judge-55 role (wrongly) — a
   precision gain too small to pay recall for.

Practical yield at these cutoffs, on the sample week: ~6 strong companies
and ~5 worth-a-look per 7 days. If the human approves mainly strong-tier
contacts, that is roughly 26 companies/month at 2–3 Hunter credits each —
inside the 100/month tier, which the old bar's ~47/month was not.

Derived from one 56-posting week; validated the same day against a second
sample — see below.

## Validation against a second window (2026-08-24, 167 new judgements)

The postings were already on the Pi; only judge scores were missing. All
unjudged postings from the six normal-flow days of the trailing week were
judged (167 new, 180 scored total after cache overlap with the tuning
week — so this is a partial, not fully independent, second sample).

Two burst days (08-21: 934 postings, 08-24: 545) were deliberately
excluded: those are backfill dumps from companies newly added to the
tracker (Compass ×60, Jane Street ×56, …), whole boards landing at once.
The trickle days are the population the gate actually runs on daily.

**What held.** The 65 spend bar: of 142 postings below it, exactly one
had judge ≥65 (a judge-75 full-stack role at composite 63). The top of
the ranking is exactly right — nine of the top ten are judge 75–92
FDE/data-engineering/founding roles.

**What softened.** The empty 70–72 composite gap was a small-sample
artifact; it filled in. The strong tier (≥72) now holds 23 postings, of
which 5 (~22%) have judge ≤62 — all the same failure shape: Staff-level
roles at large companies (company-w ×2, company-x, company-u product, company-y)
whose deterministic dimensions max out while the judge correctly
discounts the seniority stretch. Worst case: one company-w posting at
composite 82 with judge 62, outranking company-z' judge-87 FDE role.

**Decision: cutoffs unchanged.** Moving strong to 75 would remove most of
that contamination on this sample but would also have demoted two
judge-72/78 roles from the tuning week — false negatives, the costlier
error under the broad-gates ethos. The practical consequence is about
*ordering*, not gating: within the shown list, a Staff-seniority-inflated
composite can outrank a better-fitting role, so the judge's one-line
reason shown alongside the ranking is the corrective, not a tighter
threshold.

## Ground truth: the roles that actually interviewed him (2026-08-24)

The strongest available calibration signal: mine Gmail for real interview
processes, judge those postings, and see whether the judge's scores track
what the market actually did. Six genuine processes were found (excluding
AI-interview platforms, mass invites, and one scam). Scored on the real
posting text where it exists:

| Role | Judge | Process reached |
|---|--:|---|
| company-m, Forward Deployed Engineer | 92 | interviewed (via Paraform) |
| company-n, Forward Deployed Engineer | 88 | full loop, 3-hour NYC onsite |
| company-o, Full-Stack SWE (deployed) | 87 | live — technical round scheduled |
| company-p, Forward Deployed Engineer | 85 | recruiter screen |
| company-q, Software Engineer (fintech) | 58 | first round, then rejected |
| company-r, SWE II (finance) | 55 | full loop, rejected at the end |
| company-s, Python Dev (staffing) | 8 | recruiter screen only |

Score order matches process-depth order seven for seven. The four roles
the judge rates strong (85–92) are the ones that pursued him — all four
are forward-deployed/embedded-engineer shapes, the same shape that now
tops the pipeline's ranking. The two it rates below the 65 line are the
two that rejected him. company-n is the instructive case: its posting demands
3+ years customer-facing (a hard miss on paper at 1.2 years), but the
judge scored the substance — NetSuite/SAP/Salesforce ERP integration work
in Python — and the market agreed, running him through a full onsite. That
is the judge weighing substance over stated years, the exact inverse of
the deterministic dimensions' Staff-inflation failure above.

company-p's posting states no visa sponsorship, now or in the future — a
hard eligibility fact the gate does not currently screen for at all
(`qualify/eligibility.py` checks only full-time status and non-frontend
titles). Worth a note, not a fix forced by one data point: see "What's
explicitly not considered" below.

Caveats: n=7; the set is survivorship-biased toward roles he chose to
apply to; interview outcomes reflect more than role fit. These scores were
not written to the pipeline's cache — they are not tracker postings.

## The judge was never wired into a live run (found and fixed 2026-08-25)

Everything above about semantic fit — that it's the dimension telling a
Product Manager from a backend engineer, that it fixed the ranking — was
true only of the manual `outreach_run.py judge` runs done by hand during
the tuning work in this doc. `.claude/skills/outreach/SKILL.md`, what
"run outreach" actually executes, went straight from `prepare` to Agent 1
and never called `judge`/`judge-save` at all.

Caught live: a same-day run scored a Technical Account Manager posting at
company-aa 89 and it wasn't in `.cache/semantic.json`, nor was any other
posting from that day's window. With semantic_fit (weight 30, the
largest dimension) absent, `score_job` drops it from the denominator
rather than scoring it zero, so the composite ran on the remaining 70
points alone — none of which can tell a customer-facing role from an
engineering one. A same-titled TAM posting elsewhere in the cache, judged
properly, scored 12 with reason "a customer account-management role."

Fix: `SKILL.md` Step 1 now runs `judge` before every `prepare`, invoking
`relevance-judge` on whatever's unjudged and saving before scoring. No
code changed — `outreach_run.py judge`/`judge-save` already existed and
worked; they were just never called by the thing that runs the pipeline
day to day.

## Years-of-experience hard eligibility (2026-08-25)

Confirmed directly by Yash: 2.5-3 years counting everything (AMD,
Finaptive intern, Finaptive full-time), and a posting naming 4+ isn't a
stretch worth scoring or a contact-finder call — "at that point the range
doesn't even matter." Two changes:

- `qualify/profile.py`'s `YEARS_OF_EXPERIENCE` moved from 2 (a
  resume-inferred "defensible middle" between 2.5 counting internships and
  1.25 post-grad) to 3, the number stated directly.
- `qualify/eligibility.py` gained a third hard rule, `check_years`: a
  posting whose extracted minimum exceeds `YEARS_OF_EXPERIENCE` is
  ineligible, same tier as full-time-only and no-frontend-titled. A
  posting stating no minimum is eligible — absence of a number is not a
  claim he falls short of one.

Also fixed in `qualify/extractor.py`: the years-minimum regex matched
`(\d+)\+?\s+years?` against the whole text, so a written range like
"3-5 years" matched at "5 years" (no space before the dash on "3-5") and
silently took the range's ceiling as its floor. A range pattern now runs
first and takes the lower bound. This bug predates today's change and was
already feeding the wrong number into `_score_experience_fit` for every
ranged posting — the fix corrects scoring, not just the new hard gate.

Checked against the ground truth above before landing: company-n's posting
wants "3+ years" — under the new rule that's eligible (`3 <= 3`), matching
what actually happened (full onsite loop). The rule as implemented does
not contradict the one real data point available.

## Visa-sponsorship hard eligibility (2026-08-25)

The gap noted below as "not yet confirmed to be worth the complexity on a
single observed instance" got a second instance the same day: reviewing
the top-scored postings from a fresh run by hand, company-e (score 92) stated
"US citizenship required; no visa sponsorship available." Yash's reaction
was unambiguous: "if they ask US citizenship theres no point moving
forward." Two real postings (company-p, company-e) is enough — implemented as
a fourth hard rule, same tier as the other three:

- `qualify/eligibility.py` gained `check_visa_sponsorship`: a posting
  whose extracted `visa_sponsorship` is `"no"` is ineligible. Silent on
  the question is eligible — same reasoning as the years rule, absence of
  a statement isn't a claim it's unavailable.
- `qualify/extractor.py`'s phrase list extended beyond "no visa" /
  "unable to sponsor" / "do not sponsor" to also catch "citizenship
  required" / "must be a us citizen" / "us citizens only" — a posting can
  refuse sponsorship by demanding citizenship without ever using the word
  "visa."
- `qualify/profile.py`'s `NEEDS_VISA_SPONSORSHIP` flipped `False` → `True`.
  It had been left `False` specifically to award the `preferences_fit`
  visa sub-score's 2 points unconditionally — the same "soft signal buries
  a hard fact" mistake the years gate fixed hours earlier, just worth 2
  points instead of the 10 experience_fit carries.
- Fixed along the way: `_score_preferences_fit`'s visa sub-score read
  `job_data.get("visa_sponsorship")`, but nothing ever set that key on
  `job_data` — the field only ever existed on `extracted_reqs`, computed
  by the extractor. The sub-score was silently inert regardless of the
  `NEEDS_VISA_SPONSORSHIP` flag's value. Now reads `extracted_reqs`.

## What's explicitly not considered

Anything not in the dimension table above — this gate does not currently
weigh company stage/funding recency, interview-process length, or
anything Paraform's richer data captures (deferred, see
`docs/decisions.md`).
