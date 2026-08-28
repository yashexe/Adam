# Contact-strategy research: verification and ingestion record

**What this is.** On 2026-08-26 a deep-research model was given
`contact-strategy-brief.md` and returned a 17-page report ("Job Search Cold
Outreach System", in `~/Downloads`). Its claims were not ingested at face
value: most of its citations are cold-email SaaS content marketing, one is
literally `unknown_url`, and its central factual claims were checked
adversarially — seven parallel verification agents working from primary
sources (platform documentation, live API probes, DNS, case law), plus an
empirical sweep of this repo's own 625 cached board responses. This file
records what survived, what didn't, and what the pipeline should change.

**The one-line answer to the original question** ("is the current
contact-finding the best we can do?"): the *finding* is close to the
achievable ceiling — the report's proposed replacement substrates turned
out to be fabricated or auth-walled — but the *selection* should become a
human-arbitrated slate instead of a single blind pick, and the biggest
verified losses are downstream of contact choice entirely (no follow-up,
no recipient-specific personalization, human-triggered latency).

---

## Claim-by-claim verdicts

| # | Report claim | Verdict | What verification found |
|---|---|---|---|
| 1 | Move sending to Google Workspace + custom domain; personal Gmail is "highly scrutinized" | **Refuted** | Google's 2024 rules trigger at ~5,000 msgs/day/domain — this system sends ~20/month. gmail.com mail auto-passes SPF/DKIM/DMARC (verified in DNS). A fresh domain is the one setup with *documented* spam suspicion (Spamhaus ZRD auto-blocklists new domains; warmup consensus even among the report's own cited vendors). The "91–93% inbox placement" figure is unsourced vendor marketing. **Stay on personal Gmail.** |
| 2 | Ashby/Greenhouse public APIs "natively expose hiring team metadata" (Design B's cornerstone) | **Refuted, empirically** | Zero person/recruiter/hiring-team fields across all 625 cached board responses in this repo (238 Ashby + 387 Greenhouse orgs), the official docs, live board fetches (Ramp 134 jobs, GitLab 219), full hosted-page `__appData` dumps, and field-by-field probes of Ashby's public GraphQL schema ("Cannot query field" for every person-field guess). `hiringTeam` exists only in **authenticated employer-side** APIs (Ashby `application.info` → 401; Greenhouse Harvest → 401). The report conflated employer APIs with public ones. |
| 3 | Apify actors extract LinkedIn's "Meet the hiring team" card without login; legally safe | **Refuted** | Logged-out LinkedIn job pages and the guest API return zero person data (0 of 12 postings probed). The named cookie-less actor (`apimaestro/linkedin-job-detail`) has **no poster field in its own output schema**. Any actor that does deliver the card must run authenticated sessions — the exact conduct LinkedIn sued Proxycurl out of existence over (filed 2025-01-24; shutdown 2025-07-04, confirmed). The card itself is real but opt-in, auth-walled, present on ~a quarter of postings. |
| 4 | "2.4x higher interview conversion emailing hiring managers vs recruiters" | **Refuted** | The cited "2026 State of Cold Emailing Recruiters" does not exist; the citing site is a $1.99 Chrome-extension marketing page whose blog renders "Loading articles...". The 8.5%/5.6%/4.2% reply-by-seniority numbers are third-hand B2B *sales* figures whose own cited origin doesn't contain them — and they contradict the 2.4x claim's direction (they say recruiters reply *most*). |
| 5 | Rewrite the ladder: recruiters only at >50 employees; allow `careers@` at <15 | **Rejected** | Driven by the fabricated numbers above. The one sound structural fact: first recruiter hires arrive ~40–50 employees, so at tiny companies the founder/CTO simply *is* the hiring manager — which the current ladder already accommodates. `careers@` at this pipeline's targets (all run an ATS by construction) can feed straight into the ATS queue via Greenhouse Maildrop — the exact queue this project routes around — and an alias defeats both the role-account refusal and the name-conflict identity check. **Ladder and careers@ ban stay.** |
| 6 | Add follow-up sequence (day 4 bump, day 10 final) | **Direction survives; numbers don't** | The one robustly replicated finding in the space: a single follow-up meaningfully lifts total replies (Backlinko/Pitchbox 12M-email study: +65.8% in SEO outreach; Woodpecker 20M: ~9%→~13%). Job-specific evidence (Accountemps survey of 300+ HR managers; Ask a Manager) says candidate follow-ups are *expected* — but slower: 5–7 business days, not day 4; diminishing returns after 2–3 touches, candidate context stricter. The "40% of callbacks on touch 2–3" figure is an unsourced marketing invention — discard it, keep the direction. |
| 7 | Apply first, email immediately after, lead with "I just applied" | **Refuted** | The report's own citation says wait 3–5 days, contradicting "immediately." Practitioner opinion is genuinely split; the "ATS data-entry friction" mechanism is a one-step Maildrop forward per Greenhouse's own docs (and the attached résumé is exactly what Maildrop parses). No study supports leading with application status; at a small startup it voluntarily reclassifies the sender as applicant #300. **Outreach stays independent of applying**; same-day parallel apply is an optional human choice, never a pipeline gate, never the draft's opening. |
| 8 | Replace/augment Hunter with BounceBan/Scrubby for catch-alls | **Refuted for this system** | Real vendors, but marketing accuracy claims (98% with an 83%-coverage asterisk the report dropped), "ping sourcing" misdescribes both tools, Scrubby's 24–72h window contradicts the speed premise, and neither provides the per-address *identity* data that is the one thing this pipeline uniquely needs from Hunter (the company-c wrong-person defense). Hunter's `accept_all` handling is also richer than the report claims (distinct status + confidence + sources + names). |
| 9 | Slate of 2–4 contacts, human arbitrates at draft review | **Adopt** (no external evidence needed) | Directly addresses the stated trust problem; near-zero marginal human time at ~3 approvals/run; converts the agent's committed pick into a reviewable decision. |
| 10 | Demolish the research→draft wall for personalization | **Adopt, modified** | The 142%-lift number is vendor lore, but the direction (recipient-specific > posting-generic) is consistent practitioner consensus, and the failure mode (creepy over-personalization) is avoidable by restricting to public professional context. Pass a *curated personalization context*, not the research trail. |
| 11 | Scheduled runs to capture day-0 | **Concurs with existing roadmap** | Already the roadmap's top item; nothing new, mild independent support (recruiters review in batches and stop at 4–5 viable candidates). |
| 12 | Bayesian per-role reply tracking, no pixels | **Adopt method, not its priors** | Beta-binomial updating and a credible-interval kill-switch are sound at this volume; no-pixels is right (noisy, spam-risk, creepy in 1:1 mail). The report's specific priors cite `unknown_url` — set modest priors from the verified benchmark ranges instead. `outreach/replies.py` is already the substrate. |
| 13 | Drop the résumé PDF attachment ("deliverability death trap"), link a hosted résumé instead | **Refuted — the report's #1 recommendation inverts** | Gmail's own docs never treat a clean PDF from an authenticated account as a spam signal (PDF is not on the blocked-attachment list; attachments appear nowhere in the spam-reasons or sender-guideline pages). Enterprise gateways scan-then-deliver (Proofpoint: most mail within ~2 minutes, 15-minute max, quarantine only on an actual threat verdict; Mimecast: safe-file conversion at worst) — and sub-50-person startups mostly run stock Google Workspace anyway. The proposed fix is actively *worse*: Proofpoint's and Google's own security research document unsolicited cloud-document links as a leading phishing pattern that filters and recipients are trained to distrust, links get URL-rewritten and reputation-scored where attachments don't, and DocSend's email-gate mimics credential harvesting. The "+150–200% interview lift" appears in no source at all. **Keep attaching the résumé; mention it in the body so it's expected.** |
| 14 | State TN eligibility in the first paragraph for recruiters | **Rejected** | The documented rejection mechanism is ATS screening checkboxes, not recruiters inferring visa status from email — and this pipeline's emails bypass that checkbox entirely. The drafter's existing ban on visa/citizenship mentions stands. |

Also confirmed as stated: Proxycurl's shutdown (2025-07-04, after LinkedIn's
federal suit); PDL-class datasets refresh monthly-to-quarterly, i.e. stalest
exactly for week-old hires at <50-person startups — which closes the door on
"buy the org graph" as a replacement for Agent 1, not just on the report's
version of it.

## What this means for the original distrust

Agent 1's *identification* is not the weak link: it went 5/5 on real people,
the deterministic verify layer catches its address errors, and every
alternative substrate the report proposed — public ATS APIs, logged-out
LinkedIn, Apify actors, pre-scraped datasets — was shown to carry **zero
accessible identity data** for exactly this population. Agentic web research
plus Hunter verification is not a placeholder; it is what the data landscape
actually permits. The legitimate trust gap is that the agent *commits* to
one target invisibly. That is fixed with process (the slate), not with a
different finder.

## Change plan

Ordered; 1–3 are the substance. Each keeps the approval gate and per-company
dedup untouched except where explicitly flagged.

1. **Slate + human arbitration (the trust fix).**
   `contact-finder.md` returns a ranked `candidates` array (1–3 people,
   each with name/role/evidence/per-person confidence) instead of a single
   pick; `confidence: none` still skips. The `outreach` skill shows the
   slate at the existing pre-spend review step; the human confirms or swaps
   before Agent 2 runs. Store the slate alongside `source_notes`. Fold in
   the roadmap's Hunter-roster head start: `prepare` hands Agent 1 the
   domain-search roster so it researches toward known-real mailboxes —
   the verified, legitimate version of the report's "deterministic
   substrate."
2. **One follow-up bump (the yield fix — the one deliberate policy
   change).** After 5–7 business days with no reply (`replies.py` check,
   which also gates on bounces), the company surfaces as a bump candidate;
   the drafter writes a two-line reply *in the existing Gmail thread*; it
   lands in Drafts and a human sends it, same as everything else. Hard cap:
   one bump, ever, per company. This amends PIPELINE.md's "at most one
   outreach attempt per company, full stop" to "one contact, at most two
   touches" — same person, same thread, so per-company dedup is untouched.
   Needs Yash's explicit sign-off since it reopens a stated policy.
3. **Personalization context (draft quality).** Agent 1 returns an
   optional `personalization_context`: one or two public, professional
   facts about the recipient or company (their blog post, talk, launch,
   funding). The skill passes it to the drafter; the drafter may use at
   most one, woven naturally. The wall stays for everything else —
   `source_notes`, confidence, verification detail still never reach
   Agent 2. Professional context only; nothing from personal life or
   social accounts.
4. **Review-card flags.** Surface Hunter's `accept_all`/`risky` label
   prominently on the slate/review card (it already passes through
   labeled; make the human decision explicit: send anyway / alternate
   contact / skip). Optionally note on the card that a manual glance at
   the posting's LinkedIn page in Yash's own logged-in browser sometimes
   shows the actual req owner — a ten-second human step; never automated,
   never his session in a tool.
5. **Scheduled runs** — unchanged as the roadmap's top architectural item;
   the report adds urgency, not new design.
6. **Measurement (cheap, later).** Keep no-pixel reply tracking; when
   enough sends accumulate, summarize per-role Beta posteriors in
   `outreach_run.py replies`. Priors set modestly (order of 10% reply for
   a well-targeted candidate email; treat as priors, not benchmarks).

**Explicit non-changes:** personal Gmail stays; Hunter stays (no
BounceBan/Scrubby); no ATS-API contact step; no LinkedIn automation of any
kind; ladder and careers@ ban stay (with one clarification for Agent 1:
at sub-~30-person companies the founder/CTO *is* rung 1, not a lower
rung); outreach stays independent of applying; drafts never mention
application status in the opener and never mention visa status at all.

---

## Addendum: the report's headline recommendation, inverted

The report ranked "remove the PDF attachment + apply first" as its #1
change, at "+150–200% interview conversion, High confidence." After
verification, every leg of that recommendation failed independently:

- The PDF-attachment danger is bulk-sender lore from conflict-of-interest
  vendors; Gmail's, Proofpoint's, and Mimecast's primary documentation
  contradict it for this context (verdicts in row 13).
- The hosted-link replacement matches a documented phishing pattern and
  adds link-reputation risk an attachment doesn't carry.
- The apply-first mandate is contradicted by the report's own citation on
  timing (row 7).
- The +150–200% magnitude appears in no source, including the report's own.

Had the report been adopted unverified, the pipeline would have dropped
the attachment recruiters expect, adopted the email shape recipients are
trained to distrust, moved sending to an unwarmed domain that spam
infrastructure explicitly flags (row 1), and re-gated outreach behind the
ATS queue it exists to beat — four regressions sold as the top three
fixes. The verification pass is why the change plan above keeps the
sending infrastructure and message envelope exactly as built, and takes
from the report only what independently survived: the follow-up bump, the
slate, the personalization context, and urgency on scheduling.
