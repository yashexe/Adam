# Deep-research brief: contact selection for automated job-search outreach

## Your task

You are researching one question for the maintainer of a working, private, single-user
job-outreach system:

**Given a fresh job posting at a specific small company, who should a cold outreach
message go to — and through what mechanism should a system identify, verify, and reach
that person — to maximize the probability of landing an interview?**

The system already works end to end. This is not a debugging request. The question is
whether its choices are the right ones, and what the best version of this system looks
like — which may be an evolution of the current design, a replacement for it, or a
reshaping of the pipeline around it.

## Rules of engagement

- Your report will be ingested by the engineering agent that maintains this system. It
  has full code context; you do not need to produce code. Write for that consumer:
  dense, structured, evidence-first. No generic job-search advice, no padding, no
  restating this brief back.
- Do not ask clarifying questions. Where this brief is silent, state your assumption
  inline and proceed.
- Do not treat the current design as a baseline to defend or anchor on. It is one point
  in the design space. "Discard the current approach and do X instead" is an acceptable
  conclusion if the evidence supports it. So is "the current approach is near the
  ceiling and the leverage is elsewhere in the pipeline." Argue from evidence and
  mechanism, not from deference to what exists.
- Distinguish three things throughout: measured evidence, practitioner folklore, and
  your own inference. Most published cold-outreach data comes from B2B sales. A job
  seeker writing to a company that just published a hiring signal is a different
  situation — the recipient has actively asked for candidates. Transplant sales
  evidence carefully and say explicitly when you are transplanting.
- It is late August 2026. Evaluate tools, data vendors, and platform policies as they
  stand now, and flag where your knowledge may be stale.
- Use these stage names so your output maps cleanly onto the system: **Discovery,
  Qualify, Contact Selection, Verification, Draft, Review/Send, Log.**

## The goal

Interviews. Specifically: first-round conversations for one candidate, generated from
fresh job postings, faster than the normal application queue. Replies are an
intermediate signal; applications submitted, emails delivered, and contacts found are
not success metrics at all.

## The sender

- Software engineer, 2.5–3 years of total experience (about 1.2 years full-time since
  graduating in 2025; the rest is a year-long internship at a large semiconductor
  company and a startup internship). Degree in electrical engineering.
- Founding engineer in fact at a small fintech startup: wrote the large majority of the
  platform, a multi-tenant financial-data integration system moving millions of records
  a day for real enterprise clients, including a production LLM pipeline. Deep,
  specific stories for fintech, healthtech (EHR integration), data infrastructure, and
  applied AI.
- Based in Toronto, targeting New York. Canadian citizen, TN-visa-eligible — no
  lottery, fast processing. This matters because recruiters screen on work
  authorization, and postings that say "no sponsorship" frequently mean "no H-1B"
  without having considered the TN route.
- Target roles: backend, forward-deployed, and full-stack engineering at small
  companies — often under 50 people, often recently funded. Not frontend-titled roles,
  not senior/staff roles.
- Assume no meaningful warm-intro network in New York. (If your research concludes that
  systematically building warm paths beats cold contact on a weeks timescale, that is
  in scope — say so.)
- His four real interview processes to date all came from forward-deployed-engineer-shaped
  roles, reached through ordinary applications and a recruiting marketplace — not
  through cold email. His roughly a dozen historical hand-written cold emails have
  untracked outcomes. **The cold-email channel itself is a bet, not a validated result.
  Challenging it is in scope.**

## The system today

An automated pipeline, run on demand, with a human approving every message:

1. **Discovery** (fixed, upstream): a poller watches the Ashby and Greenhouse job
   boards of New York companies every 10 minutes. A new posting is visible within
   minutes of going live. This is the system's core edge: reaching someone before the
   applicant flood.
2. **Qualify**: deterministic eligibility rules (full-time only, no frontend-titled
   roles, no stated minimum above 3 years, no US-citizenship requirement), then an LLM
   judge scores fit 0–100 against his profile. Postings scoring ≥65 are worth spending
   on. Yield: roughly 10–15 qualifying companies per week; the human reviews the ranked
   list and typically approves ~3 per run for the stages below.
3. **Contact Selection** (the stage in question): an LLM research agent with live web
   search. Detailed below.
4. **Verification**: deterministic email-address resolution and checking. Detailed
   below.
5. **Draft**: a second LLM agent writes the email in the sender's own voice from a
   curated profile document, picking one relevant story. Drafts are linted, specific,
   short, and ask for a call. Draft quality is believed good; do not spend your effort
   on copywriting advice.
6. **Review/Send**: the draft lands in the sender's Gmail Drafts with his résumé
   attached. He edits and sends by hand. No code path can send.
7. **Log**: the company is marked claimed the moment a draft is created. At most one
   outreach ever per company (policy detail below). Reply tracking per contact role
   exists but has no data yet.

### Contact Selection, in detail

Input: company name/domain plus the posting (title, URL, location, funding hint).

The agent runs 15–30 web searches and page fetches per company against public sources:
the posting itself, team/about/leadership pages, engineering blogs, funding
announcements and press, the company's GitHub, conference talks, public professional
profiles. It cannot access sources behind logins (LinkedIn is effectively out of
reach).

Its target-preference ladder, verbatim in spirit:

1. A named hiring manager or engineering lead **for this posting**, if one can be
   identified. They own the vacancy and can act alone.
2. A recruiter or talent partner who owns this req. Reasoning: reading candidate email
   is their job, filling the role is how they are measured, so they are the likeliest
   responder after the hiring manager. In-house preferred over agency.
3. The engineering leader (CTO / VP Engineering / Head of Engineering).
4. A founder, at companies small enough that founders read inbound (roughly seed to
   Series A, under ~30 people).
5. A senior engineer on the relevant team, if nobody above is findable.

Hard rules: no shared inboxes ever (careers@, info@, jobs@ — "a named recruiter is a
person, an alias is a queue"); contact-data aggregators (RocketReach, ZoomInfo, Apollo,
ContactOut) may corroborate that a person holds a role but are never evidence for an
address; the agent must not construct email addresses (that was moved out of the agent
after a failure — see below); fabricating a name to fill the schema is the worst
failure; "no defensible person found" is an accepted answer that skips the company.

Output: name, role, company domain, any real address actually observed at that domain
with its source, a confidence grade **about the person** (high / medium / low / none),
and 2–4 sentences of source notes, which are stored for human review.

### Verification, in detail

Deterministic code, one email-verification API (Hunter.io) as its data source:

- Fetch the domain's known address roster and pattern (Hunter domain-search returns
  every address it has seen at the domain, each with the name attached when known).
- Render the domain's own pattern for the contact's name (for example `{first}.{last}`).
- Refuse the rendered address if Hunter attributes that exact mailbox to a *different
  person's* name — this catches the wrong-person collision described below.
- SMTP-verify the result. Fall back to the agent's observed address if pattern
  resolution fails, unless it is a shared inbox, which is refused outright.
- Only a confirmed-undeliverable address blocks. Catch-all domains (the norm at small
  companies), risky, and unknown results pass through to the human, labeled.

### Measured behavior and incidents (the honest texture)

- In a five-company live spike, the agent found a real, verifiable named person at
  5/5 small NY companies (a CTO, two VPs of Engineering, a Head of Engineering, a
  cofounder). Zero fabrications, zero generic-inbox fallbacks. Findability is not the
  weak point.
- But when the agent also constructed addresses, it was right 3/5; domain-pattern
  resolution in Verification corrected it to 5/5. Both failures came from generalizing
  a domain's convention from one genuine but unrepresentative observed address.
- The agent's confidence did not predict address correctness — its one "high" was the
  wrong address. (Its confidence about the *person* is unmeasured against outcomes.)
- Wrong-person collision: rendering a domain's pattern for the intended contact
  produced a real, deliverable mailbox belonging to a different person with a similar
  name at a ~100-person company. Caught by hand; now checked automatically against the
  roster's names.
- In one live run, 2 of 3 companies burned the full research spend and then died at
  Verification (one name conflict, one unresolvable address). Research effort and
  address reachability are currently discovered in the wrong order.
- The recruiter dispute: recruiters were initially banned as targets (reasoning: "the
  pipeline exists to route around the ATS"). The ban was reversed after it discarded a
  named Technical Recruiter and a Head of Recruiting at one company in favor of an IC
  engineer whose team couldn't even be confirmed. The ladder position of recruiters
  versus hiring managers versus founders is argued, not evidenced.
- Outcome data: effectively none. One pipeline email sent (two days ago), one earlier
  hand-sent email, two drafts pending, five candidates discarded at human review, zero
  replies recorded so far. **Every targeting choice currently rests on priors, and
  there is no feedback loop yet.**

### Already-identified improvement ideas (evaluate them; don't re-derive them)

- Seed the agent with the Verification stage's domain roster *before* research begins
  — the roster of known-real mailboxes and names — so it researches toward people
  already known to be reachable instead of finding out afterward that its pick isn't.
- Move the pipeline from human-triggered runs to scheduled runs, so the
  speed-to-posting edge is realized in practice, not just in design.

### Current policies you are explicitly invited to challenge

- **One outreach per company, forever**, claimed at draft time. Rationale: two
  qualifying postings 30 minutes apart must not produce two emails to the same person;
  one-per-company is the conservative key. Consequences: emailing a recruiter
  permanently forecloses the hiring manager at that company and vice versa; there are
  no follow-up sequences; a no-reply is terminal.
- **Outreach is independent of applying.** The email replaces joining the ATS queue
  first rather than supplementing an application. Rationale: gating on applying
  reintroduces the delay the system exists to remove.
- **Contact research never informs the draft.** The drafting agent receives only the
  contact's name and role — a deliberate wall so identification and persuasion can't
  contaminate each other.
- **Email is the only channel.**
- **Shared inboxes are always refused**, even at 10-person companies.

## Fixed constraints (do not spend effort relitigating these)

- One real human sender, under his real identity. No personas, no deception, no
  fabricated referrals or pretexts.
- Every outgoing message is individually reviewed and sent by the human. Nothing fully
  automated ever touches a recipient. Volume is therefore precision-scale — tens of
  messages per month, not thousands. This is an identity constraint of the project, not
  a temporary limitation to engineer away.
- Discovery stays as described: fresh NY postings from Ashby/Greenhouse boards. (You
  may note what adjacent discovery data would buy for contact selection, but do not
  redesign discovery.)
- Data access must be legitimate: public information and properly licensed commercial
  data. For ToS-restricted sources — LinkedIn above all — analyze the real options and
  their actual risk (official APIs, licensed data vendors, compliant third parties,
  small manual steps performed by the human) rather than assuming either "never touch
  it" or "just scrape it."
- Assume no meaningful constraint on engineering effort, tooling budget, model
  capability, or build complexity. Do not optimize for cheap. Optimize for interviews.

Everything not listed above is open: the agent architecture, the ladder, the
verification stack, the channel, the one-per-company rule, the walls between stages,
and the shape of the pipeline itself.

## The research questions

Answer all ten. Number your answers to match.

**Q1 — Who should the message go to?** For a cold message from a 2.5–3-year engineer
about a specific posting published in the last day or two: what does the best available
evidence say about which recipient maximizes (a) reply probability and (b)
advance-to-interview probability? How does the answer change with company size (<15,
15–50, 50–200, 200+), with whether an in-house recruiting function exists, and with
role shape (forward-deployed vs. backend vs. founding engineer)? Does sender seniority
interact — does a founder or CTO respond better or worse than a recruiter to a
relatively junior engineer? Evaluate the current five-rung ladder rung by rung and
propose the corrected version. Include the edge case: at very small companies, is the
blanket refusal of shared inboxes (careers@) actually right, or do tiny teams triage
those seriously?

**Q2 — One contact, several contacts, or a sequence?** The system sends exactly one
message to one person per company, ever, with no follow-up. Sales practice says
multi-threading and follow-up sequences carry most of the yield; job-search norms may
differ. What is the evidence for (a) polite follow-up sequences to the same person
(how many, what spacing), (b) contacting a second person at the same company after
silence (who, after how long), and (c) simultaneous multi-threading? Weigh the
reply-rate gains against annoyance and reputation risk inside a small company where
recipients talk to each other. If sequences or second contacts win, specify the
protocol concretely enough to implement.

**Q3 — Is email even the right channel?** Compare cold email against LinkedIn
(connection note, InMail), against applying immediately *plus* a same-day email
referencing the application, and against any other channel with real evidence behind
it (community Slack/Discord, X/Twitter, engaging with the company's public work).
For each: reply-rate evidence for job-seeking specifically, deliverability or
reachability limits, automation and ToS constraints given the fixed rules above, and
how the choice interacts with who the recipient is (recruiters live in LinkedIn and
the ATS; founders live in email). Should the apply-vs-outreach independence policy be
revisited — does a visible application in the ATS make the cold email land better or
worse with each recipient type?

**Q4 — What is the best identification substrate in 2026?** The current agent does
open-web research and cannot see LinkedIn. Evaluate, alone and in combination: LLM
web-research agents (current approach); commercial enrichment and people-data vendors
(Apollo, Clay, People Data Labs, Hunter, RocketReach, ZoomInfo, and whatever else is
now state of the art) keyed on a company domain; LinkedIn-native approaches within the
legitimacy constraint (official APIs, licensed resellers, Sales Navigator driven by
the human, one-off manual lookups the human performs when the system asks); and — 
verify this hypothesis specifically — hiring-team identity exposed by the job posting
infrastructure itself: Ashby hosted pages sometimes name the recruiting contact,
LinkedIn job posts often show the poster ("meet the hiring team"), Wellfound shows
recruiters, and ATS APIs may carry more than the public page renders. Which of these
surfaces exposes the req owner deterministically, how often, and how fresh? Coverage
claims should be specific to sub-50-person companies, where commercial coverage is
known to degrade — that measured collapse is why the current design is agentic at all.

**Q5 — Address resolution, verification, and deliverability.** Given a chosen person
at a small company: what is the state-of-the-art stack for finding and verifying their
mailbox (multi-provider waterfalls, catch-all handling, identity confirmation against
the wrong-person collision described above)? Is the current policy — only
confirmed-undeliverable blocks; catch-all and unknown pass through labeled — the right
risk posture, and what bounce rate should be tolerated from a personal Gmail account
before it damages his sending reputation? Also address two mechanics questions with
evidence: does attaching a résumé to a *first* cold email hurt deliverability or
response, and is sending from a personal Gmail address the right call versus
alternatives?

**Q6 — Where should the selection decision live?** Today one agent researches and
commits to a single pick; a human sees the choice only via stored source notes, after
the draft exists. Alternatives: the agent returns a ranked slate of 2–4 candidates with
evidence and a separate step (deterministic scorer, second judge, or the human)
chooses; deterministic pre-ranking from enrichment data with the agent filling gaps;
or a human-in-the-loop card between Contact Selection and Draft — the human already
reviews every draft, so the marginal cost of also confirming the *target* may be near
zero. Which allocation of judgment produces the best picks per unit of human
attention, and which produces trust that the pick was right — the maintainer's stated
problem with the system?

**Q7 — How much should the target shape the message?** Evidence on personalization
depth versus reply rate: does referencing the recipient's own work (their blog post,
their talk, their team's launch) measurably beat posting-level personalization, and
where does it tip into creepy? Should the wall between contact research and drafting
be opened so recipient-specific context flows into the draft — and what changes by
recipient type, since a recruiter, a founder, and a hiring manager reward different
first paragraphs? Keep this at the strategy level (what information should reach the
draft stage); the writing itself is out of scope.

**Q8 — Does the speed premise hold, and what does timing change?** The system's core
bet is that contacting within hours of a posting appearing beats joining the queue.
Find whatever evidence exists on outreach timing relative to posting age (and on
early-applicant advantage generally, as the nearest proxy). Does the *best recipient*
change with posting age — for example, recruiter triage in the first days, hiring
manager attention later? Practical send-timing (day, hour) is worth one paragraph at
most.

**Q9 — How do we learn anything at 10–25 sends per month?** Reply-rate A/B testing is
statistically hopeless at this volume. Design the measurement approach honestly: what
to instrument (replies per contact role exist; open tracking does not — should a
job-seeker's one-to-one mail carry tracking pixels at all?); which external benchmarks
to adopt as priors, with numbers; decision rules for when to abandon or switch
strategies at this sample size (Bayesian updating from informed priors, sequential
decision rules, pooling across contact types); and proxy evaluations that don't need
send volume — for example, retrospective expert adjudication of contact choices
against a rubric, or comparing the system's pick to what a professional recruiter or
sourcing expert would have chosen for the same 20 companies. State what reply and
interview rates this system *should* expect per recipient type if it is working, so
underperformance is detectable within 4–8 weeks.

**Q10 — Is contact selection even the binding constraint?** Given everything above —
a validated fit-scorer, good drafts, ~10–15 qualifying companies a week, one
carefully-chosen recipient each, no follow-ups, email-only, zero outcome data — rank
the available levers by expected marginal interviews: better target selection, more
contacts per company, follow-up sequences, channel changes, apply+email pairing,
faster triggering, message changes, or something this brief hasn't considered. If the
maintainer's distrust of contact selection is aimed at the wrong stage, say so
plainly and defend the reranking.

## Deliverables

Produce, in this order:

1. **Executive verdict** (one page maximum): is the current Contact Selection design
   close to the achievable ceiling or not; the three changes with the largest expected
   effect on interviews, ranked, each with a magnitude estimate and your confidence in
   it.
2. **Answers Q1–Q10**, numbered, each ending with a one-line "what this means for the
   system."
3. **Two to four coherent designs** for the contact stage and its surroundings. At
   minimum: (a) the strongest incremental evolution of the current design; (b) a
   data-vendor-first redesign where enrichment does the heavy lifting and the agent
   fills gaps; (c) at least one design that changes the pipeline's shape —
   multi-contact, sequenced, multi-channel, or application-integrated. For each:
   the target-selection policy as a concrete decision procedure (by company size and
   situation, implementable as written), the data stack with named vendors and rough
   costs, the verification approach, expected effect on interviews with reasoning,
   failure modes, and the first thing that would reveal it isn't working.
4. **A recommendation**: which design, why, what to build first, and which assumptions
   are doing the most load-bearing work.
5. **A measurement plan** per Q9, with the concrete priors and decision thresholds you
   would adopt.
6. **Annotated sources**: for each load-bearing claim, what the source actually
   measured, on what population, and why it does or does not transfer to a job seeker
   cold-contacting a small company that posted a role this week.

Throughout: numbers with provenance; label folklore as folklore; where evidence is
absent, reason from mechanism and say that is what you are doing. Sanity-check
recommendations from the recipient's side — what a recruiter, a founder, and a hiring
manager at a 20-person company each actually experience when this message arrives on
day zero of their posting.
