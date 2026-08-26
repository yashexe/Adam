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

Weights as of the 2026-08-26 rebalance (see "Dead weight in the
deterministic composite" below for what changed and why):

| Dimension | Weight | What it measures |
|---|---:|---|
| Semantic fit | 30 | LLM judgement of profile/posting fit, not embeddings — resolved, see "Semantic fit, implemented" below |
| Role/title fit | 25 | Target role family, title similarity |
| Required skills fit | 25 | Required skills found in the profile — UNKNOWN when the posting yields fewer than 3 extracted skills |
| Preferences fit | 5 | Location, remote policy, salary, visa — raw sub-scores 5/4/4/2, rescaled |
| Domain/company fit | 5 | Industry/domain overlap |
| Preferred-skills bonus | 10 | Nice-to-have skills present |

(Experience fit, weight 10 in the Instaply original, was deleted on
2026-08-26 — the `check_years` hard eligibility rule reads the same
posting minimum against the same profile years, so by the time a posting
reached the scorer this dimension could only return UNKNOWN or full
marks.)

**Non-compensatory gate:** if required-skills coverage is under 30%, the
total score is capped at 49 regardless of how well everything else scores —
location and preference points cannot buy back a fundamental skills
mismatch. Ported from Instaply as a calibrated, tuned rule; since
2026-08-26 it inherits the dimension's ≥3-skill evidence floor, so it can
no longer fire off a coverage ratio computed over one or two extracted
keywords.

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

## Decision tiers (re-tuned 2026-08-24, re-derived 2026-08-26)

- 69–100: strong match
- 64–68: worth a look
- <64: not strong enough to spend an Agent 1 call on

Instaply's inherited cutoffs were 85/65 — thresholds for a different
purpose (deciding whether to *email the user*) over a different score
distribution. Once semantic fit landed, 85 became unreachable: the judge's
0-100 maps onto the scorer's SEMANTIC_SIM_FLOOR..CEIL band, which
compresses the composite, and the maximum observed over the 56-posting
judged sample is 83. Nothing was ever "strong" under the old tier. The
2026-08-24 re-tune set 72/65 empirically from the joint composite/judge
distribution (see "Where the cutoffs come from" below).

The 2026-08-26 scorer rebalance shifted the whole composite distribution
down about 4 points, so both cutoffs were re-derived on the 305-posting
validation corpus rather than assumed to survive: the judge-confirmed
strong cluster now bottoms out at 69, and 64 is the highest spend bar that
loses zero judge≥70 postings (the lowest one sits exactly at 64).
`DEFAULT_MIN_SCORE` in `outreach/pipeline.py` moved 65→64 to match — see
"Dead weight in the deterministic composite" below for the full
derivation.

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
"US citizenship required; no visa sponsorship available." Yash's first
reaction was "if they ask US citizenship theres no point moving forward" —
but he was explicit that the rule is narrower than "no sponsorship":
**only an explicit citizenship requirement disqualifies.** "No sponsorship"
alone does not, because he may be TN-eligible (Canadian) and a company
saying "we don't sponsor" is very often ruling out H-1B without being
aware of, or meaning to rule out, TN. "Sometimes they may say no
sponsorship available (not aware of the TN visa route) — ONLY if they
mention US citizenship, should we back off."

- `qualify/extractor.py` gained a distinct `citizenship_required` boolean,
  separate from the existing `visa_sponsorship` yes/no/unknown field —
  matched against "citizenship required" / "must be a us citizen" / "us
  citizens only". The two are deliberately not merged: general
  no-sponsorship language stays a neutral signal, only a stated
  citizenship requirement is disqualifying.
- `qualify/eligibility.py` gained a fourth hard rule,
  `check_citizenship_required`: a posting with `citizenship_required=True`
  is ineligible. Silent, or merely "no sponsorship," is eligible.
- `qualify/profile.py`'s `NEEDS_VISA_SPONSORSHIP` stays `False` on
  purpose — general "no sponsorship" language should not cost points,
  soft or hard, for the same TN-eligibility reason.
- Fixed along the way (harmless while the flag above stays `False`, but a
  real bug regardless): `_score_preferences_fit`'s visa sub-score read
  `job_data.get("visa_sponsorship")`, but nothing ever set that key on
  `job_data` — the field only ever existed on `extracted_reqs`, computed
  by the extractor. The sub-score was silently inert no matter what
  `NEEDS_VISA_SPONSORSHIP` was set to. Now reads `extracted_reqs`.
- `.claude/agents/drafter.md` gained an explicit ban on mentioning visa
  status, sponsorship, or citizenship in a draft — that question is
  settled upstream, before a company ever reaches Agent 1, and preempting
  an objection the reader never raised is exactly the "expects to be
  doubted" failure mode the drafter's contract already warns against
  elsewhere.

## Dead weight in the deterministic composite (2026-08-26)

Hand-tracing company-ab's Forward Deployed Engineer posting
(a recruiter pull, judged 72) through the scorer exposed a chain of
problems in the deterministic 70 points, each confirmed by executing the
real code rather than by inspection:

- **`taxonomy.contains_keyword` treated `-` as a word boundary**, so
  "go-live" and "go-to-market" both matched the `Go` language keyword.
  company-ab's posting mentions no real skill keywords at all; its entire
  extracted `required_skills` list was `["Go"]` from "go-live", he
  "missed" it, and the 0/1 coverage ratio fired the non-compensatory gate
  — an otherwise-~68 posting capped at 49 by a false positive. GTM
  phrasing is common in exactly the FDE postings this pipeline targets
  most.
- **The gate had no minimum-evidence floor** — a coverage ratio over one
  extracted keyword was treated as seriously as one over fourteen.
- **`experience_fit` (weight 10) had become structurally dead**: the
  `check_years` eligibility rule (added 2026-08-25) excludes any posting
  whose stated minimum exceeds his years, and the dimension read the same
  extracted minimum against the same profile years, so it could only ever
  return UNKNOWN or a perfect 10 — a constant, quietly inflating every
  score that reached the scorer.
- **`preferences_fit` (weight 15) had 10 of its 15 raw points
  unconditionally free** under the current profile (`remote_policy:
  "any"`, `min_salary: None`, `needs_visa_sponsorship: False`); only the
  5-point location sub-score ever discriminated — and even that drops out
  of the denominator when a posting lists no locations, letting a posting
  score 15/15 on zero signal.

### The fix

Five changes to `qualify/taxonomy.py` and `qualify/scorer.py`:

1. `HYPHEN_SENSITIVE_KEYWORDS = {"go"}` — for keywords in this set (and
   only these), a hyphen is a joiner, not a boundary. Deliberately not
   blanket: "AWS-based", "LLM-powered", "REST-based" are real mentions a
   global hyphen rule would break. `go` is the one keyword that is also an
   ordinary English verb.
2. `REQUIRED_SKILLS_MIN_EVIDENCE = 3` — a posting yielding fewer than 3
   extracted skills gets `required_skills_fit = UNKNOWN` (weight dropped
   from the denominator) instead of a ratio over noise, and the gate
   consequently cannot fire. This went further than first planned (the
   plan was to guard only the gate): validation showed the ungated
   dimension was equally noisy in both directions — a judge-78 Founding
   Engineer posting scored 0/25 off a 2-keyword sample (stranding it at
   55), while 43 of 305 corpus postings scored a free 25/25 by matching
   one or two generic keywords (three judge-15 iOS postings rode that to
   the spend bar).
3. `experience_fit` deleted outright, not zero-weighted — it can never
   disagree with `check_years` by construction, so there is no future in
   which it becomes a live signal again.
4. `preferences_fit` weight 15→5. The sub-score logic is untouched on
   purpose: the free points trace to profile tunables that are documented
   as changeable, and if they change the sub-scores come back to life at
   a weight that now matches their real discriminating range.
5. The freed 20 points went to `role_title_fit` (15→25) and
   `required_skills_fit` (15→25). The earlier worry that role_title was
   near-constant did not survive contact with the data: over the corpus it
   correlates with the judge at r=+0.48, the strongest deterministic
   dimension by a wide margin (required_skills sits at +0.11), while its
   known failure mode (full marks on a title-substring match like
   "Software Engineer - Mobile, iOS") argues against pushing it above
   parity with required_skills.

### Validation

Offline before/after over the real corpus: every key in
`.cache/semantic.json` (441 judged postings) resolved against the cached
board responses in `.cache/boards/` directly — bypassing the 24h TTL so
historical text stays usable — rebuilt into `job_data` by the real
builders and scored by the real path. 305 postings survive eligibility
(134 excluded by years, 2 by citizenship, identical before and after).

- Mean composite delta −4.7 (the dead always-full dimensions leaving the
  numerator), median −4.
- Gate firings 6 → 1; every false-positive-driven cap gone, the one
  survivor is a genuine <30% coverage over a real sample.
- At the new spend bar of 64: **zero** judge≥70 postings below the bar
  (the old scorer at its old bar stranded a judge-78 posting; the min
  judge≥70 composite now sits exactly at 64), and judge<50 leakage
  halved from 20 to 10 — what leaks is dominated by the known
  Staff-seniority caveat cluster (company-ac ×3) documented in the
  2026-08-24 validation section.
- Tier cutoffs re-derived on the same corpus rather than assumed:
  strong 72→69 (the judge≥80 cluster bottoms at 69, with two stragglers
  at 68 and 65), spend bar 65→64. `TIERS` in `qualify_run.py` and
  `DEFAULT_MIN_SCORE` in `outreach/pipeline.py` updated together.
- Ground truth: the company-m Forward Deployed Engineer posting (the
  one interview-verified posting with saved text in-repo,
  `harvest/from_paraform_pipeline/ny_roles_mds/`) scores 94, comfortably
  strong — and its "visa sponsorship not available" line correctly does
  not disqualify it under the narrow citizenship-only eligibility rule.

Still open, deliberately: `preferred_skills_bonus` only detects a
preferred section via the literal headers "preferred"/"nice to have"/
"bonus" (safe — a miss returns UNKNOWN, never penalizes), and it shares
the small-sample noise the required dimension just lost its exposure to.
Lower stakes at weight 10 and bonus-shaped; a synonym-expansion follow-up,
not part of this pass.

## What's explicitly not considered

Anything not in the dimension table above — this gate does not currently
weigh company stage/funding recency, interview-process length, or
anything Paraform's richer data captures (deferred, see
`docs/decisions.md`).
