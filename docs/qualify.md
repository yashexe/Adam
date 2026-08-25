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

Derived from one 56-posting week. Worth re-checking once a few more
judged windows accumulate, but no longer inherited from a different tool
for a different job.

## What's explicitly not considered

Anything not in the dimension table above — this gate does not currently
weigh company stage/funding recency, interview-process length, or anything
Paraform's richer data captures (deferred, see `docs/decisions.md`).
