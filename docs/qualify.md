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

## Scoring — the judge's 0-100, directly (since 2026-08-26)

The QUALIFY score is the `relevance-judge` subagent's 0-100, batched over
a window and cached per posting (`qualify/semantic.py`,
`.claude/agents/relevance-judge.md`). There is no composite around it:
the Instaply-harvested weighted composite that wrapped the judge for the
project's first days was measured to be a lossy copy of the judge and
deleted — the evidence and the deletion are documented in "The judge
becomes the score" below, and the composite's own history (including the
2026-08-26-morning rebalance that preceded the deletion by hours) is kept
in the dated sections underneath it.

Each judgement carries a small rubric besides the score — `shape`
(core-engineering / forward-deployed / customer-facing / research /
management / non-engineering), `seniority` (fits / stretch / above),
`domain` (strong / some / none) — plus a one-line `reason`, so ranking
and review surfaces show *why* without re-reading the posting.

Three frozen calibration postings (`qualify/anchors.py`) ride unlabelled
in every judge batch: an interview-ground-truth FDE posting (expected
75-100), an adjacent DevOps/SRE posting (35-70), and a marketing posting
(0-25). `judge-save` warns when an anchor lands outside its band — the
drift alarm for a score that now carries everything. Across the 8-batch
corpus re-judge the anchors scored 82-93 / 48-68 / 2-4 — all in band.

Deterministic code keeps the jobs it is right for: the hard eligibility
rules (`qualify/eligibility.py` — facts, not fit), extraction for
eligibility inputs and display metadata (`qualify/extractor.py`), and
per-company dedup. An unjudged posting has **no score at all** — it is
surfaced as unjudged, never ranked on partial information.

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

## Decision tiers (on the judge's scale since 2026-08-26)

- 70–100: strong match
- 65–69: worth a look
- <65: not strong enough to spend an Agent 1 call on

Derived from the re-judged 303-posting corpus and the interview ground
truth, on the judge's own scale (see "The judge becomes the score" below
for the full derivation):

- **70 (strong)**: the corpus distribution is bimodal — a dense strong
  cluster runs 72-93 (50 postings) with 69-71 completely empty below it.
  70 splits the empty gap with a point of slack on each side, the same
  gap-splitting method the composite-era cutoffs used.
- **65 (spend bar, `DEFAULT_MIN_SCORE`)**: the judge's scale itself
  draws this boundary — 65-84 is "a real fit with some distance", 40-64
  is "adjacent... not one where his specific background is an advantage",
  and an adjacent posting is not worth a cold email whose premise is a
  specific story. Every posting that produced a real conversation sits
  at 82-92; the two lowest-scored processes both ended in rejection
  (company-q 58; company-r 55 as originally scored on partial text, 70 on the
  full-text re-judge of 2026-08-31). A third rejection landed at 85 on
  2026-08-31, so the bar predicts *whether a conversation happens*, not
  whether it converts.

Every earlier tier value (Instaply's inherited 85/65, the 2026-08-24
re-tune to 72/65, the 2026-08-26-morning re-derivation to 69/64) lived on
the composite's scale and died with it — their histories are preserved in
the dated sections below.

## Where an LLM is involved

One place: the relevance judge, whose score is the score. Instaply's
harvested `judge.py` offered two LLM refinements on top of its
deterministic composite — a 60/40 blend and an embedding-based
semantic-fit dimension. The embedding was replaced with a direct LLM
judgement on 2026-08-23 ("Semantic fit, implemented" below), the blend
was never revived, and on 2026-08-26 the composite around the judgement
was deleted entirely ("The judge becomes the score" below) — the
refinement turned out to be the product.

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

**Judge stability, measured (2026-08-26):** a stratified 40-posting sample
re-judged fresh against its cached scores gave a test-retest rank
correlation of +0.97, 35/40 within ±10, and 3–4 band flips at the
decision-relevant lines — stable enough to rank on, with a ±7 per-score
jitter and a small batch-composition drift worth anchoring if the judge
ever carries more weight.

**Years framing corrected (2026-08-26):** until this date the judge's
prompt (and `PROFILE.md`) said "about 1.2 years full-time" — the post-grad
tenure alone, not the 2.5–3 counting the AMD year and internships that
Yash confirmed on 2026-08-25 and that the `check_years` eligibility rule
uses. The judge was double-penalizing senior-titled postings that
eligibility had already vetted (a stated 3-year minimum is fine by his own
policy). The prompt now states both numbers, says to judge against 2.5–3,
and explicitly defers stated-minimum enforcement to the upstream
eligibility rule. **Judgements cached before this date carry the stricter
1.2-years framing** — they are not invalidated (ordering was 7/7 right on
ground truth regardless), but borderline senior-titled postings judged
before 2026-08-26 sit a few points lower than the corrected prompt would
put them, and a wholesale re-judge is deliberately deferred to whenever
the judge next changes shape rather than spent now.

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
inside the 100/month tier, which the old bar's ~47/month was not. (Since
2026-09-02 the mailbox check itself is a free SMTP probe for most domains
and a company costs about 1 Hunter credit, the roster lookup — see
`outreach/verify.py`.)

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
| company-af, Forward Deployed Engineer | 92 | recruiter screen (live, added 2026-08-31) |
| company-ag, Forward Deployed Engineer | 92 | recruiter outreach — inbound; posting states "US citizen (required for government work)" and the recruiter confirmed directly that it does not apply (live, added 2026-08-31) |
| company-ak, Forward Deployed Engineer | 85 | **the pipeline's first reply** — cold email 2026-08-31, the co-founder/CTO answered the same morning (live, added 2026-08-31) |
| company-ah, Forward Deployed Engineer | 90 | recruiter conversations — **three independent inbound reach-outs** (company-ah in-house and two unrelated agency recruiters) (live, added 2026-08-31) |
| company-n, Forward Deployed Engineer | 88 | full loop, 3-hour NYC onsite |
| company-aj, Forward Deployed Engineer | 88 | recruiter screen — inbound, recruiter reached out (live, added 2026-08-31) |
| company-o, Full-Stack SWE (deployed) | 87 | live — technical round done, awaiting the next (as of 2026-09-02) |
| company-p, Forward Deployed Engineer | 85 | screen + two team conversations, **rejected 2026-08-31** — the first rejection from the 85+ band |
| company-al, Forward Deployed Engineer | 85 | intro call — inbound message 2026-08-27, awaiting next step (live, added 2026-08-31) |
| company-am, Founding Engineer | 82 | **the pipeline's first end-to-end conversion.** Cold email 10:34 → CEO replied 12:31 introducing the engineering lead → 30-minute meeting booked 13:50, same day (live, added 2026-08-31) |
| company-q, Software Engineer (fintech) | 58 | first round, then rejected |
| company-r, SWE II (finance) | 70† | full loop, rejected at the end |
| company-s, Python Dev (staffing) | 8 | recruiter screen only |

† Re-judged 2026-08-31 on the complete posting text (supplied by Yash,
with the exact resume that application used); the original 55 was scored
on partial reconstructed text. Anchors rode the re-judge batch, all
in-band (93/42/4). Shape: core-engineering.

On the original seven, score order matched process-depth order seven for
seven (the 2026-08-31 rows are live, early-stage processes — no depth to
order yet). The roles the judge rates strong (85–92) are the ones that
*engage* him — all seven are forward-deployed/embedded-engineer shapes,
the same shape that tops the pipeline's ranking. The inbound rows are the
strongest kind of confirmation available: recruiters reached out *to him*
for postings the judge independently scores 88 and 90 — company-ah three
separate times, from three unrelated sources, in the same week.

**The 85+ band took its first rejection on 2026-08-31** (company-p, 85 —
screen plus two team conversations, then declined). Until then every
concluded process at 85+ had either pursued him or was still live, and
that claim appeared throughout this file.

The right correction is narrower than "the band is weaker than we
thought," per Yash 2026-08-31: **offer conversion is not what this gate
is accountable for.** The judge answers "is this a job worth being
contacted about," and its measurable output is whether a real
conversation happens. What happens inside the room is interview
performance, comp alignment, who else they were talking to, and timing —
none of which a posting's text predicts and none of which the score
claims to. Every 85+ posting has produced a real conversation; both
concluded processes at ≤70 were rejections. That is the claim the data
supports and the claim the gate should be judged on.

The one place the gate *is* implicated in a late-stage loss: if it
scores a posting strong whose real bar is a discipline he doesn't have,
the mismatch surfaces as a failed interview rather than as a missing
reply. company-an (judged 40, 2026-09-01) is the counter-example of the
gate working — an FDE-titled role whose actual requirements were
production data science, caught before any spend.

Two provenance facts, per Yash 2026-08-31. **Recency:** of the seven
85+ processes, all but company-n started within the past month (and all but
company-r within two) — the pursuit band is accelerating, not
historical residue. **Channel:** company-n and company-p were sourced by
ashby-ny-tracker itself — the discovery half of this system has already
produced two real interview processes, including a full onsite, before
the outreach half has recorded its first reply. The rest split between
Paraform (company-m) and inbound recruiters (company-aj, company-ah);
channel-of-origin is now worth tracking on every future process, since
"which channel produces interviews" is the number the whole pipeline
exists to move.

The company-r re-judge (55→70 on full text) redraws the failure band in an
informative way: the 59–84 band is no longer empty — its one data point
is a core-engineering 70 that produced a **full interview loop and no
offer**. That is exactly what the judge's own scale predicts for "a real
fit with some distance": interviews happen, conversion doesn't. The
depth-ordering claim weakens accordingly (a 70 went deeper than an
85-scored screen), but the conversion ordering sharpens: every process
at 85+ pursued him and none rejected him; both concluded processes at
≤70 ended in rejection, and both were core-engineering-shaped. One era
caveat cuts across the concluded rows: company-q/company-r were run on the
February resume ("Software Engineer", mechanism-dense bullets, no
client-facing story), while the entire 85+ live wave arrived on the
overhauled August resume — outcome differences between the bands carry
both the score gap and the positioning gap. company-n is the instructive
case: its
posting demands 3+ years customer-facing (a hard miss on paper at 1.2
years), but the judge scored the substance — NetSuite/SAP/Salesforce ERP
integration work in Python — and the market agreed, running him through a
full onsite. That is the judge weighing substance over stated years, the
exact inverse of the deterministic dimensions' Staff-inflation failure
above.

company-af (2026-08-31) adds a new wrinkle to the visa rule: its posting
states no sponsorship "including H-1B, OPT/CPT, TN" — the first observed
posting that names TN as excluded, which is precisely the route the
narrow `check_citizenship_required` rule exists to protect (the rule's
rationale is that companies saying "no sponsorship" usually don't mean
TN). Under the current rule this posting stays eligible, and the market
data cuts both ways: the stated policy would exclude him, yet the process
reached a recruiter screen anyway. Left as a flag, not a rule change —
one posting, and screens evidently happen despite stated policies.

**company-ag (2026-08-31) turns that flag into a real problem with the
rule.** Its posting states "US citizen (required for government work)" —
unambiguously the fact `check_citizenship_required` exists to catch — and
a recruiter reached out anyway, and when asked directly said the
requirement does not apply. The judge scores the posting 92.

Two separate findings sit inside that:

1. **The detector has a phrasing gap.** `qualify/extractor.py` matches
   "citizenship required", "must be a us citizen", and "us citizens only".
   company-ag's phrasing ("US citizen (required for government work)")
   matches none of them, so the posting passed eligibility by accident
   rather than by design.
2. **Closing that gap would have been the wrong outcome.** Note the
   posting arrived via recruiter outreach, not through the tracker, so
   nothing was actually discarded — this is a test result, not an
   incident. But a *tracker* posting worded this way and caught by a
   widened detector would be silently dropped before any human saw it,
   and clera (92) is the case where that really happened.

The stated-restriction record now reads: three postings carrying
citizenship or visa restrictions, two of which produced real processes
(company-af screen, company-ag inbound with the requirement explicitly
waived) and one of which (clera) was hard-excluded before any human
judgment. The rule was built on Yash's instruction that "if they ask US
citizenship theres no point moving forward," which remains his call — but
the evidence since is that stated restrictions are weaker predictors of
actual process than the rule assumes. Recorded here rather than acted on
unilaterally; the decision belongs to him.

company-p's posting states no visa sponsorship, now or in the future — a
hard eligibility fact the gate does not currently screen for at all
(`qualify/eligibility.py` checks only full-time status and non-frontend
titles). Worth a note, not a fix forced by one data point: see "What's
explicitly not considered" below.

Caveats: n=14 (7 concluded-or-deep, 7 live added 2026-08-31); the set is
survivorship-biased toward roles he chose to apply to (company-aj, company-ah,
and company-al, as pure inbound, are the exceptions); interview outcomes
reflect more than role fit. The company-af/company-aj/company-ah scores were judged
ad hoc with the standard anchors riding in each batch, all in-band both
times (91/52/3 and 92/52/4), and were not written to the pipeline's
cache. company-al is different in kind: a tracker posting the pipeline had
**already judged 85 in its live cache** before the process existed —
the first ground-truth row whose score predates its outcome, i.e. a
genuine prospective prediction rather than a retrodiction. He never
applied; they messaged him. company-al is also now closed in
`outreach_log` (`in_process_inbound_2026-08-27`), because the contact
happened off-email — LinkedIn message and a call — which the Gmail
prior-contact check cannot see, so without the store row a future drain
could have cold-emailed a company he is mid-process with.

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

## The judge becomes the score (2026-08-26)

The morning's "Dead weight" rebalance (above) was the third targeted fix
to the deterministic composite in three days. Asked directly whether the
Instaply-inherited design was worth keeping at all, the honest answer
came from measuring rather than arguing, and it killed the composite the
same day. The evidence, all on real data:

- **Every deviation the composite made from the judge was an error in
  the same direction.** On the 305-posting offline corpus, the
  deterministic 70 points promoted 20 postings above the spend bar that
  the judge rated below 60 — the company-ac Staff cluster ("Staff Software
  Engineer" substring-matches "Software Engineer" and collects full
  title marks), an OutSystems developer (judge 30), a Research Engineer
  (judge 20), Delivery/growth roles — and demoted exactly one borderline
  judge-65 posting. Zero cases were found of the deterministic layer
  catching a judge mistake.
- **The composite was 89% the judge already** (r=+0.886), while its
  deterministic 70 points alone correlated with the judge at just
  +0.335. Every tuning pass — including the morning's — amounted to
  making the deterministic layer agree harder with the judge. The
  endpoint of that process is the judge.
- **Ground truth ordered 7-for-7 on the judge alone** ("Ground truth"
  above), including the company-n case where the judge overruled
  years-on-paper and the market agreed.
- **The judge is stable enough to carry the score**: +0.97 test-retest
  rank correlation on a re-judged 40-posting sample ("Judge stability"
  above), and all 24 anchor scores across the 8-batch corpus re-judge
  landed inside their expected bands.

### What changed

- `qualify/scorer.py` deleted. Ranking and tiers run on the judge's
  0-100 directly (`outreach/pipeline.py`, `qualify_run.py`). An unjudged
  posting is surfaced as unjudged, never scored on partial information —
  the failure mode that produced a Technical Account Manager at 89 is
  structurally gone, not patched.
- The judge reads 3000 chars of the posting instead of 800, and returns
  a structured rubric (shape / seniority / domain / reason) alongside
  the score, so review surfaces stay explainable without a composite
  breakdown.
- `qualify/anchors.py`: three frozen calibration postings ride
  unlabelled in every batch; `judge-save` warns when one scores outside
  its known band. This is the mitigation for the new single point of
  failure — a drifted judge announces itself instead of silently
  reordering the pipeline.
- `qualify/profile.py` shrank to the one fact deterministic code still
  reads (`YEARS_OF_EXPERIENCE`, for `check_years`). The judge's profile
  is `PROFILE.md`, as it always was.
- Extraction (`extractor.py`, `taxonomy.py`) survives for eligibility
  inputs and display metadata. Eligibility is unchanged.
- The 441-entry cache was cleared and the 303 currently-resolvable
  eligible postings re-judged under the corrected years framing
  (old-vs-new r=+0.92, mean shift −1.6 — the big movers all carry
  title-vs-substance reasons in both directions, e.g. a "Software
  Engineer" title whose body describes a Principal-level anchor role
  fell 78→22, and an internal-tools builder hidden under a Product
  title rose 18→72).

### Where the cutoffs come from now

The fresh corpus distribution is bimodal: 99 postings at 0-9, 79 at
10-19, a thin adjacent band through the 40s-60s, then a dense strong
cluster from 72 to 93 (50 postings) with **69-71 completely empty**
below it. 70 splits that gap — the same method the composite-era tiers
used. 65 is the spend bar because the judge's own scale defines 40-64 as
"adjacent — not one where his specific background is an advantage", an
adjacent posting is not worth a cold email whose premise is a specific
story, and the ground truth agrees: both rejections below the line
(55, 58), all four pursuits far above it (85-92).

### The risk taken, stated plainly

Fit now rests on one LLM call. If the judge prompt, model, or
`PROFILE.md` degrades, there is no independent scoring layer to catch
it — by measurement there never effectively was one. The mitigations:
anchors in every batch (drift announces itself), judgements cached and
reviewable with reasons attached, hard eligibility still deterministic,
and the human approval gate before any spend unchanged. What was paid
for the simplification: three days of composite-tuning machinery,
deleted; what was bought: the score the ground truth actually validates,
with its failure modes fenced instead of diluted.

## The LLM-extraction composite, tested and declined (2026-08-26)

The judge-becomes-the-score decision left one real alternative untested:
maybe the old composite failed not because composites are wrong but
because its *inputs* were regex garbage. An LLM extractor pulling clean
structured facts (discipline, work style, stated skills, seniority
expectation, domain) feeding a deterministic scorer would keep the score
auditable and profile-independent — the inverse split: LLM reads, code
judges. Yash asked for it to be tested rather than argued about.

Run on a 75-posting sample from the judged corpus: all 20 known-bad
postings (unambiguous non-fits — sales, research, Staff+, management —
that the old composite had promoted), all 35 judge-strong postings
(including the two the old composite suppressed), plus mid/low bands. The
extractor was profile-blind (knew nothing about any candidate); the
deterministic scorer over its output was written and frozen *before* any
extraction ran, so it could not be overfit to the sample.

| | known-bad kept out | known-good kept in |
|---|---:|---:|
| judge (current) | 20/20 | 35/35 |
| old regex composite | 12/20 | 34/35 |
| LLM-extraction composite | 17/20 | 29/35 |

Two findings, in tension:

- **The hypothesis was half right.** Swapping regex extraction for LLM
  extraction fixed most of the old composite's disease: the Staff cluster
  died cleanly (`seniority_expectation: staff-plus`), the
  solutions/delivery/relationship roles died cleanly
  (`customer-relationship`), and the extractions themselves were accurate
  on inspection — no hallucinated facts, defensible enum calls.
- **It still loses to the judge on both sides of the bar, structurally.**
  Its three false positives (an OutSystems low-code developer at 77, a
  campus research program at 77, a bank model-validation "AI Scientist"
  at 68) are all cases where the extracted facts are *correct* but the
  verdict needs knowledge the facts can't carry — what OutSystems work
  means for a Python-backend person, what a campus program is. And its
  six lost judge-strong postings reproduce the old composite's other
  disease in softer form: stated-stack overlap penalizing strong-fit
  roles whose listed technologies differ from his (company-ad's backend role
  at 58 for matching 1 of 4 listed languages, company-ae at 57 again).
  Facts-then-formula fails exactly where judgment is the load-bearing
  step.

Cost sealed it: extraction ran ~3.2k tokens/posting vs the judge's ~1.1k,
because structured output per posting is bigger than a score plus one
line. Three times the price for a worse answer, and the judge's rubric
fields (shape/seniority/domain/reason) already carry the audit trail that
was extraction's main selling point — the current design is effectively
extraction and judgment in one call. The one advantage extraction keeps —
cached facts survive profile changes, cached judgements don't — was not
worth 3 false positives and 6 lost strong postings. Experiment artifacts
in the session scratchpad; not merged.

## What's explicitly not considered

Anything not in the dimension table above — this gate does not currently
weigh company stage/funding recency, interview-process length, or
anything Paraform's richer data captures (deferred, see
`docs/decisions.md`).
