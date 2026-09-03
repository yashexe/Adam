# Decision log

Short records for forks a future session might otherwise reopen. If it's not
here, it wasn't a real fork — don't add entries for things that only ever
had one sane answer.

---

**Outreach fires independently of applying** — not gated behind "I applied
to this role."
*Why:* gating behind an application reintroduces the exact delay the tracker
exists to remove, and isn't logically necessary — a direct reply can route
around the ATS entirely.
*Alternatives:* require a manual "I applied" signal before drafting starts.
*Consequence:* applying stays a fully separate, optional, user-initiated
action with no pipeline dependency on it either way.

---

**Dedup is keyed on company alone** — not (company, posting), not (company,
contact).
*Why:* a company posting two qualifying roles 30 minutes apart must not
produce two emails to the same person. Company-level is the conservative
default even where a company might have multiple legitimate hiring
contacts.
*Alternatives:* per-posting (rejected — spams the same contact), per-contact
(rejected — more precise but adds complexity nobody asked for).
*Consequence:* a pending draft gets its referenced posting swapped in place
if a better one arrives before review; a company already sent to just logs
silently on any later match. See PIPELINE.md's rate-limiting policy for the
full behavior.

---

**Two separate agents (find-contact, draft), split at a deterministic
verify step** — not one combined agent.
*Why:* research and persuasive writing are different skills with different
failure modes; combined into one continuous run, "did it find the right
person" and "is the email good" can't be independently evaluated or tuned.
The verify step also needs structured data in/out, which is clean between
two invocations and awkward mid-reasoning inside one.
*Alternatives:* single agent doing both in one pass.
*Consequence:* Agent 1 never writes persuasive copy; Agent 2 never
second-guesses whether the contact is real. See docs/agents.md.

---

**Contact-finding is agentic (web search), not a pure email-finder API
call.**
*Why:* checked Hunter.io and Apollo.io directly. Both are viable and
affordable (Hunter's free tier read 100 verification credits/month when a
key was actually created on 2026-08-23, not the 50 this research recorded;
cheapest paid tier $34/mo), but independent benchmarks show coverage degrades sharply for
companies under ~50 employees — domain searches frequently return zero
results or only generic addresses. That's precisely the population this
project targets: small, freshly-funded companies, where being early is the
whole point.
*Alternatives:* Hunter/Apollo as the primary discovery mechanism.
*Consequence:* an API still earns a narrow role — pattern hints and cheap
verification at stage 4 — just not as the discovery mechanism itself.
*Measured 2026-08-23:* the split held, but not quite as predicted. Hunter
returned a usable address pattern for 4 of 5 small-company domains — better
coverage than the benchmarks suggested — and correcting Agent 1's inferred
address took it from 3/5 to 5/5. On the fifth domain Hunter had no
addresses at all and the agent was the only source. Neither replaces the
other; the API owns the pattern, the agent owns the person.

---

**Instaply harvested, not revived as a live service.**
*Why:* same treatment as job_search_automation. Instaply is a full FastAPI
app with a heavy ML dependency footprint (sentence-transformers, torch) —
running it as a standing service duplicates infrastructure for no reason
when the actually valuable part is ~200 lines of scoring logic.
*Alternatives:* call its `POST /api/matches/jobs/{job_id}` endpoint live;
adopt the whole app wholesale.
*Consequence:* `docs/qualify.md`'s scoring gate is a port of Instaply's
formula, not a dependency on Instaply running anywhere.

---

**job_search_automation harvested, not extended in place.**
*Why:* it's a single-target, hand-edited-per-run script with no persistence
— there's no automatable core to extend, only a resume, drafting rules, and
working SMTP-send code worth keeping.
*Consequence:* Adam supersedes it; job_search_automation itself
is not touched going forward except for the resume-currency fix already
applied (see harvest/NOTES.md).

---

**Paraform pipeline harvested but integration deferred.**
*Why:* explicit user call — "Ashby/Greenhouse OK for now, let's not change
that part yet." Paraform is a real fourth discovery source (richer than
either Ashby/Greenhouse or Instaply on interview-process detail) but adding
it is a discovery-layer change to ashby-ny-tracker, out of scope for this
pass.
*Consequence:* the scrape/enrich scripts and 443-role dataset sit in
`harvest/from_paraform_pipeline/` as reference; not wired into anything.
Revisit only when discovery sources are explicitly back on the table.

---

**Outreach state lives in a local `outreach.db` on the Mac — superseding
the "no separate database" decision below.** *(2026-08-23)*
*Why:* the original decision was sound under its assumptions and those
assumptions turned out to be wrong. It was made to avoid standing up new
infrastructure, before it was established that the live `tracker.db` sits
on the Pi while this pipeline runs on the Mac. Under two hosts, same-file
became the expensive option: cross-host writes, a WAL + busy_timeout
migration on a live database, and a second writer on a file whose owner
holds a lock for 4-7 minutes of every 10. The dependency is one-directional
— outreach reads tracker data, the tracker never reads outreach state — so
a read-only edge is all that is needed.
*Alternatives:* the original (tables in `tracker.db`), rejected on the
above; syncing a copy of `tracker.db` to the Mac, rejected because a second
drifting copy is exactly the failure mode the single-DB rule existed to
prevent.
*Consequence:* the Pi is touched read-only via `?mode=ro` over SSH and
`poll.py` is not modified at all. Reliability arguably improves: the
dedup log is the pipeline's one safety-critical record, and it now lives on
a backed-up machine rather than a microSD card with documented WiFi flap.
Revisit only if outreach state ever needs to change tracker behaviour.

---

**Data lives in ashby-ny-tracker's `tracker.db` directly — no separate
database.** *(superseded 2026-08-23, see above)*
*Why:* explicit user call ("isn't there a database configured? choose
whatever is easiest") — reuses the existing SQLite file, schema pattern,
and SMTP credentials rather than standing up new infrastructure.
*Consequence:* `pending_outreach`/`outreach_log` are new tables in the same
file as `companies`/`seen_jobs`/`pending_alerts`. See PIPELINE.md's data
model section.

---

**A curated profile document, not RAG.** *(2026-08-23)*
*Why:* the story bank is about 1,700 words and fits in every prompt with
room to spare. Retrieval exists for corpora that do not fit; using it here
would mean selecting three chunks by embedding distance and hoping the
right story surfaced, when the model choosing from the whole set is both
simpler and better. Picking the right story for a company is a judgement
call, not a similarity search.
*Alternatives:* embedding the Finaptive repo and retrieving per draft
(rejected, adds a failure mode to solve a problem that does not exist);
pasting résumé facts into each prompt (rejected, the facts then drift
between callers and go stale silently).
*Consequence:* `PROFILE.md` is the single source of truth and must stay
small enough to read whole. If it outgrows a prompt, cut stories rather
than reaching for retrieval. The retrieval-shaped work was real but
one-time: mining the repo and sent mail into the document.

---

**Two hard eligibility rules on QUALIFY, despite the no-exclude-filters
rule.** *(2026-08-23)*
*Why:* full-time-only and no-frontend-titled-roles are not precision
tradeoffs on ambiguous matches, which is what that rule protects against.
They are facts about what the user is available for, stated directly. An
internship is not a weak match, it is not a match.
*Consequence:* `qualify/eligibility.py` exists, tests the title only, and
must stay tiny. Frontend as one component of a role still qualifies, which
is why parentheticals are stripped and full-stack titles are exempt.

---

**No sector filter (FinTech/AR-AP or otherwise) layered on top of the
QUALIFY score.**
*Why:* job_search_automation's "Target Companies" list was never an actual
filter — it was prose in a README for a human to read manually, so there
was nothing to preserve. Instaply's harvested scoring gate is the real bar;
adding a sector filter on top would be an unrequested second gate.
*Consequence:* QUALIFY is purely the scoring threshold. Any company/role
that clears it qualifies, regardless of industry.

---

**Recruiters are a first-class contact target** — ranked second, below only
a named hiring manager for the posting.
*Why:* they were banned outright until 2026-08-24, on the one-line reasoning
that "the entire point of this pipeline is routing around the ATS queue."
That confused the channel with the person: a direct email to a named
recruiter is not the ATS queue. Filling the req is how their work is
measured and reading candidate mail is their job rather than an
interruption from it, which plausibly makes them the likeliest responder
after the hiring manager. The old rule had a measured cost — at company-h it
discarded a named Technical Recruiter and a Head of Recruiting in favour of
an IC engineer whose team could not be confirmed.
*Alternatives:* keep the ban (rejected — it was asserted in a parenthesis,
never argued, and no decision entry existed); allow them only as a
last-resort fallback (rejected explicitly by Yash — a fallback label
understates them and would have Agent 1 reaching for vaguer contacts
first).
*Consequence:* dedup is still per-company forever, so emailing a recruiter
still forecloses the hiring manager at that company. That trade is now
made deliberately rather than by omission, and `outreach_run.py replies`
records reply rates per contact role so the ordering can eventually be
settled with data instead of argument.

---

**Replies are tracked** — `outreach_log` records what came back, not just
what went out.
*Why:* the log held company, address, outcome and timestamp, where
"outcome" described how a message was sent rather than what it achieved.
That made the central question — does any of this work, and for which kind
of contact — permanently unanswerable, and it surfaced when an argument
about recruiters could not be settled either way.
*Alternatives:* infer replies by hand from Gmail (rejected — never happens,
and cannot aggregate).
*Consequence:* `outreach/replies.py` walks the Gmail thread of each sent
message and counts any message not written by Yash. Thread-based rather
than sender-based, so a recruiter forwarding to a hiring manager who then
replies still counts. Bounces are classified separately, since a bounce is
not silence — it is evidence the address was wrong, which is the one
failure `outreach/verify.py` cannot currently catch.

---

**The judge's 0-100 is the QUALIFY score — the deterministic composite is
deleted, not down-weighted.** *(2026-08-26)*
*Why:* measured, not argued. On a 305-posting corpus every deviation the
Instaply-inherited 70 deterministic points produced against the judge was
an error in the same direction — 20 postings promoted above the spend bar
that the judge rated below 60 (Staff-title substring inflation,
generic-keyword matches), one borderline demotion, zero cases of the
deterministic layer catching a judge mistake. The seven real interview
processes ordered 7-for-7 on the judge alone, and a re-judged 40-posting
stability sample held +0.97 test-retest correlation. The composite was a
lossy copy of its own largest input: every tuning pass amounted to making
the deterministic layer agree harder with the judge, and the endpoint of
that process is the judge.
*Alternatives:* keep tuning the composite (rejected — the 2026-08-26
rebalance was the third fix in three days, each patching a failure mode
the judge did not have); LLM extraction feeding the deterministic scorer
(rejected — keeps the machinery whose value could not be demonstrated);
down-weight instead of delete (rejected — a dimension that only ever
subtracts has no correct weight but zero).
*Consequence:* deterministic code keeps the jobs it is right for — hard
eligibility facts, extraction for eligibility inputs and display, dedup —
and the fit judgement rests on one LLM call, batched and cached. The new
single point of failure is mitigated three ways: frozen anchor postings
ride in every batch and `judge-save` warns when they land outside their
known bands; the judge reads 3000 chars instead of 800; and the human
review gate before any spend is unchanged. Tier cutoffs live on the
judge's own scale, re-derived from ground truth.

---

**The QUALIFY score is the judge's 0-100 — an LLM-extraction composite was
tested and declined, not just argued away.** *(2026-08-26)*
*Why:* after the regex composite was deleted as a lossy copy of the judge,
the strongest remaining alternative was an LLM extractor feeding a
deterministic scorer (LLM reads, code judges — auditable,
profile-independent). Tested on a frozen 75-posting benchmark: it beat the
old regex composite decisively (17/20 vs 12/20 unambiguous non-fits
excluded) but lost to the judge on both sides of the bar (judge 20/20 and
35/35), at ~3x the tokens per posting. Its residual failures need
knowledge, not facts — an OutSystems seat extracts as "engineering-ic"
and only judgment knows it isn't his job.
*Alternatives:* pure judge (chosen); regex composite (deleted same day);
extraction composite (declined on this measurement); judge/composite
blends (swept — no weight where the composite demonstrably helps).
*Consequence:* the judge's rubric fields (shape/seniority/domain/reason)
carry the audit trail extraction promised. Numbers and method:
`docs/qualify.md`, "The LLM-extraction composite, tested and declined".

---

**Agent 1 returns a ranked slate of up to three candidates; the human
picks.** *(2026-08-26)*
*Why:* the maintainer's stated distrust of contact selection was diagnosed
(via the verified research pass, see below) as a process problem, not a
finding problem — the agent went 5/5 on real people, but it *committed* to
one invisibly, so a wrong ladder call surfaced only after the drafting
spend, as a discard. A slate makes the selection a reviewable decision at
the moment the human is already reviewing, and `verify-slate` resolves
reachability per candidate first (one cached domain-search; verification
credits spent only until the first deliverable candidate), so the
2026-08-25 failure mode — full research spent, then verify kills the only
pick — became a visible "pick #2 instead".
*Alternatives:* keep the single pick and tune the ladder (rejected — no
role-level evidence exists to tune it with; both of the report's
quantitative ladder claims were fabricated or untraceable); a second
LLM judge over the pick (rejected — adds a model where ten seconds of
existing human review does better).
*Consequence:* the slate is stored on the claim (`contact_slate`) and
rendered in the review UI next to source_notes; finalize still
independently re-verifies whichever candidate is chosen. Absorbed into
the ladder: at sub-30-person companies the founder/CTO *is* rung 1, a
fact about who the hiring manager is, not a preference change.

---

**One follow-up bump per company — the single-send rule amended, the only
policy the research changed.** *(2026-08-26)*
*Why:* the one recommendation from the contact-strategy report that
survived adversarial verification with independent support: follow-up
lift replicates across every dataset examined (Backlinko/Pitchbox 12M
emails, Woodpecker 20M+), and candidate-specific evidence (Accountemps
survey of 300+ HR managers, practitioner consensus) shows one polite
follow-up is expected, at job-search spacing (5–7+ business days), not
sales spacing (day 4). A no-reply under the old policy was terminal by
construction, which forfeited the most replicated effect in the space.
*Alternatives:* keep one-and-done (rejected — the single-send rule was
argued from politeness, and the evidence contradicts it); sequences of
2–3+ touches (rejected — diminishing returns after the second touch in
every dataset, and candidate norms are stricter than sales norms);
contacting a second person after silence (rejected — reopens per-company
dedup for a marginal, unevidenced gain).
*Consequence:* `bumps` classifies contacted companies (checking Gmail
live first so a bump can never cross a reply or follow a bounce); `bump`
drafts a two-sentence reply into the original thread, résumé deliberately
not re-attached; `outreach_log.follow_up_at` makes a second bump a store
error, same enforcement pattern as the dedup key. One company, one
person, one thread — now at most two touches.

---

**The contact-strategy research report's remaining recommendations,
rejected on verification.** *(2026-08-26)*
*Why:* the report's load-bearing claims were adversarially verified
against primary sources before ingestion (seven parallel verification
agents; full verdicts in `docs/research/contact-strategy-findings.md`),
and most failed. Rejected, with the finding that killed each: moving to a
Google Workspace custom domain (bulk-sender rules trigger at ~5,000
msgs/day; a fresh domain is the documented spam signal — Spamhaus
auto-blocklists newly registered domains); dropping the résumé PDF for a
hosted link (Gmail/Proofpoint/Mimecast documentation never treats a small
clean PDF from an authenticated sender as a spam signal, and an
unsolicited cloud-doc link is a documented phishing pattern); ATS-API
contact discovery (zero person fields in any unauthenticated Ashby or
Greenhouse surface — verified against docs, live boards, hosted-page
JSON, and GraphQL schema probes); automated LinkedIn hiring-team
extraction (auth-walled; the "cookie-less" vendor's own schema lacks the
field; the Proxycurl suit defines the risk); BounceBan/Scrubby catch-all
vendors (bulk-sender economics, no identity data, 24–72h latency against
a speed-premised pipeline); mandatory apply-before-email (contradicted by
the report's own citation; the ATS-friction mechanism is a one-step
Maildrop forward per Greenhouse's own docs).
*Consequence:* sending stays on the aged personal Gmail with the résumé
attached; Hunter stays the one verification vendor; outreach stays
independent of applying (same-day parallel applying remains a human
choice, and drafts never lead with it); no LinkedIn automation ever
touches this pipeline — the one legitimate consumption is Yash glancing
at a posting's hiring-team card in his own logged-in browser during
review.

---

**The drafter writes from a research digest toward a bridge — not from
one fact toward a template.** *(2026-08-28)*
*Why:* Yash deleted both pending drafts (company-j, company-k) with "the
drafts themselves suck," and reading them beside the sent company-b email
confirmed it: three individually rule-compliant emails that were one
email with the slots refilled — identical congrats-on-funding opener,
identical identity sentence, identical closer, and 8 of 9 stored
subjects following "<role> / founding engineer background". Two causes,
both structural. Agent 1 spent 50–80k tokens learning what each company
does and threw all of it away except one `personalization_context`
fact, so the drafter never had the material a company-specific email is
made of. And `drafter.md` had grown to ~530 lines of accumulated bans
around a single worked example, so the model collapsed onto the one
known-safe path: paraphrase the example — the same transcription
failure PROFILE.md's "In an email" lines caused before they were
removed. The bans killed the density problem and specificity died with
it; the company-b email, the one that broke the density rules, was
ironically the only one doing real company-specific work.
*Alternatives:* more bans (rejected — bans can prevent badness, they
cannot produce specificity); loosening the research/draft wall so the
drafter sees Agent 1's source notes (rejected — the wall's reason
stands; what the drafter lacked was company facts, which the sanctioned
window already carries — the change is volume, not boundary).
*Consequence:* `personalization_context` widened from one or two facts
to a four-to-eight-bullet public-company-facts digest (same boundary:
what the company said, never how the contact was found);
`.claude/agents/drafter.md` rewritten around the positive objective —
find the bridge, the one sentence naming the true overlap between their
problem and his work, before writing anything — with two real sent
emails as register anchors, the single model-email template removed,
an explicit anti-template section, and the mechanical density rules
delegated to `draft_lint.py`, which already enforces them; the drafter
moved from Sonnet to Opus, free on the subscription. Prompt history
("this section used to say...") moved out of the live prompt into this
log and `docs/agents.md`.

---

**Mailbox verification probes the domain's own mail server first; Hunter
sharpens what a probe cannot settle.** *(2026-09-02)*
*Why:* Hunter's month ran out on 2026-08-28, mostly on mailbox probes;
the free-tier vendor adapters added 2026-08-29 sat idle behind signups
that had not happened; every draft since landed `unverified`. The probe
every vendor sells is a plain SMTP handshake — connect to the domain's
MX, MAIL FROM the null sender, RCPT TO the address, quit before DATA —
and port 25 turned out to be open from this Mac to Google's servers,
which host 26 of the 29 domains this pipeline has ever verified.
Benchmarked against all 40 addresses Hunter had labeled: 21 identical
verdicts, 16 where the probe could only say catch-all, 3 skips
(Microsoft 365 and Proofpoint refuse or drop residential connections),
1 contradiction (a mailbox Hunter had called dead accepts mail today).
The 16 shaped the design: 12 domains, skewed to larger companies, accept
any local part from here — regardless of sender or which Google MX host
answers — while Hunter's raw response claims a definite SMTP check on
the same domains and had a valid/invalid verdict for 15 of the 16.
Five of those "valid" addresses have since been emailed for real (one
on 08-28, four on 08-31) and Gmail holds no bounce for any of them. So
on a catch-all domain Hunter knows something a live probe cannot, and
on every other domain the probe knows as much as Hunter for free.
*Alternatives:* more free-tier vendors (deferred — each is another
signup, and on a catch-all domain a vendor's probe only repeats ours);
a paid tier (rejected — Yash's explicit call); Hunter first, as before
(rejected — measured to burn a month in one run); treating the probe's
catch-all as final (rejected — throws away Hunter's better verdict on a
third of addresses); moving the probe to the Pi (moot — same home
connection, and the Mac already reaches Google's MX in under a second).
*Consequence:* `_verify_via_smtp` at the front of `_VERIFY_PROVIDERS`.
A catch-all verdict is provisional: the chain continues only through
providers flagged as able to sharpen it (Hunter), skips the ones that
cannot, and falls back to the catch-all if nobody can. Inconclusive
answers — a vendor "unknown", greylisting, any 4xx, a refusal aimed at
the sender or IP rather than the mailbox — are skips passed down the
chain and never cached; until now a vendor "unknown" was cached as
`risky` forever. ZeroBounce and MillionVerifier adapters corrected
against their live API shapes (MillionVerifier's documented demo keys
answer without an account, so that adapter is exercised; ZeroBounce's
sandbox needs a key). `outreach_run.py verifiers` shows what can answer
right now; `verify <email>` runs one address through the chain and is
the smoke test for a new key. The probe rides IPv6 — the ISP blocks
port 25 over IPv4 — so losing IPv6 turns every probe into a skip and
the chain falls through to the vendors; nothing breaks, it just costs
credits again. Cost: a company on a domain that rejects
unknown recipients now costs one Hunter credit (the roster) and no
probe; a catch-all domain still costs one probe credit; the free tiers,
when their keys exist, cover what is left.

---

**Address resolution asks the domain's own mail server when Hunter has no
roster.** *(2026-09-02)*
*Why:* the SMTP probe (previous entry) fixed verification, but the thing
Hunter's quota death actually blocked was resolution: turning a named
person into an address. `confirm_pattern` returned nothing with every
counter at zero, and on 2026-09-01 three researched companies produced
zero drafts because no candidate had an observed personal address. The
same server that answers "does this mailbox exist" can answer "which of
the conventional addresses for this name exists": render first.last,
firstlast, first, f.last and the rest, RCPT each in one session, take
the first hit. Benchmarked against 46 people Hunter's cached rosters
attribute an address to: 14 exact matches, 7 hits on a full-name address
that exists where Hunter lists a different one for the same person
(aliases, most likely; none attributed to anyone else), 0 wrong-person
hits, 1 miss, 3 unreachable (Proofpoint), 21 on catch-all domains where
the server says yes to everything. Then the real test: the three
companies blocked on 09-01 all resolved, in under seven seconds each,
with zero credits.
*Alternatives:* full-name patterns only (rejected — `{first}@` is the
convention at 12 of the 18 cached rosters, so it would miss the
small-company norm this pipeline exists for); trusting a partial-name
hit as `verified` (rejected — the company-c wrong-person case was exactly
a partial pattern labeled verified); waiting for the reset (rejected —
it recurs monthly, and the drain that killed it was one morning's work).
*Consequence:* `probe_patterns` and `_probe_rung` in `outreach/verify.py`,
shared by `resolve_address` and `resolve_slate` so the advisory and
binding resolutions cannot disagree. Ladder is now Hunter pattern →
Hunter roster → keyless probe → observed fallback. Two tiers: full-name
hits keep the probe's label; partial-name hits are `risky` with the
reason "only part of the name is in it, so a namesake would match too —
confirm the person before sending", and the reason lists any other
existing pattern. `_name_conflict` still runs when a roster is cached.
Every probed address is cached with its verdict, catch-all is cached per
domain for a week, and an MX that drops the connection is remembered
for a day (a Proofpoint slate cost 16 s per candidate before that).
Residual dependency: catch-all domains, about a third of them and
skewed to larger companies, still need Hunter's roster; the reset date
now shows in `outreach_run.py verifiers`.

---

**The pipeline runs itself; the human still picks and sends.**
*(2026-09-03)*
*Why:* every stage worked and resolution had just gone keyless, and the
whole thing still slept until someone typed "run outreach". The tracker
finds a posting within five minutes of it appearing; the pipeline then
waited hours or a day for a session, which is exactly the queue-forming
delay the project exists to beat. A morning batch was considered and
rejected for the same reason. The trigger has to be the tracker's own
rhythm, but the LLM cannot run at that rhythm: a headless session carries
tens of thousands of tokens of fixed cost and the judge carries ~8k
(profile plus anchors) per call, so firing on every arrival would
multiply the judge's weekly cost from ~130k tokens to millions. Hence two
loops: a free deterministic tick every five minutes that queues, and one
bounded LLM run when the oldest queued posting has waited 15 minutes or
five have piled up. What the run may do without a human was decided
explicitly: draft to rank one when the address is clean and the score is
70 or higher, because every pick that week had been rank one and the cost
of being wrong is a drafter call and a discard; park the slate otherwise,
and always in the 65–69 band; never draft to a `risky` address, whose
label means "confirm the person". Caps of three companies per run and
eight per day, counted in the store rather than by the prompt.
*Alternatives:* morning-only batch (rejected — throws the head start
away); fire the LLM on every arrival (rejected — the fixed-cost
arithmetic above); stop at the slate for everything (rejected — every
company would wait on the human twice, and picks had been rank one every
time); running on the Pi (still impossible: 32-bit, no `claude`).
*Consequence:* `outreach/unattended.py` (watermark, queue, budget, one
retry on failure, stale-run recovery), `bin/tick.py` (lock, 25-minute
wall clock, `--allowedTools` restricted to the skill's needs,
`--permission-mode dontAsk`, a macOS notification when drafts land),
`com.yash.adam-tick.plist` written but installed by hand like the UI
agent. The store gains `slates` (a researched company parked for a pick;
the review UI has the pick button, the next run drafts it, and the
posting text is stored so a pick survives a closed posting) and
`company_ignore` (a recruiting marketplace with 288 postings under one
slug is not a company; a human adds these with a reason, and `prepare`
says so when it skips one). Companies already in process through another
channel are closed in the store by hand so the run cannot email them.
Notion is untouched by the run: rows are for responses, never for
unanswered outreach (Yash, 2026-09-02).

---

**The tick reads the Pi at :02:30, never on the poll's beat.**
*(2026-09-03)*
*Why:* two tracker polls crashed the same morning with `database is
locked`, and both were this Mac's doing. `tracker.db` is in
rollback-journal mode, so even a `mode=ro` connection holds a SHARED lock
for the length of its query, and the candidates query is an unindexed
scan of 418k `seen_jobs` rows: 2.5 s for seven days, about 6 s for 30 or
60. The poll's commit waits five seconds and gives up. The first crash
sat under three back-to-back 30/60-day reads from a chat session; the
second was a timing measurement that started two seconds into the next
poll. A crashed poll costs nothing durable (the next one re-reads the
boards) but sends the tracker's crash email and delays that slot's
matches five minutes. The `StartInterval` schedule drifted a second or
two per cycle and would have walked the tick into the poll window on its
own within a couple of hours.
*Alternatives:* raise the poll's SQLite timeout to 30 s in the tracker
(the complete fix, since it also covers hand-run commands, but a change
to the other repo on the Pi; still worth doing); an index on
`first_seen_at` or WAL mode on the Pi (same objection); `immutable=1` on
the reader (no lock at all, but SQLite then trusts the file not to change
under it, which is exactly false mid-poll).
*Consequence:* `com.yash.adam-tick.plist` uses `StartCalendarInterval` at
minutes 2, 7, ... 57, and `bin/tick.py` sleeps `ADAM_TICK_DELAY_SECONDS`
(30, set in the plist) before its read, so the tick lands at :02:30 -- the
poll finishes by about :01:30, the next starts at :05:00. The same
day the tracker itself was fixed (ashby-ny-tracker commit aa42c00,
deployed 10:31): `tracker.db` switched to WAL mode, the poll's connection
waits 30 s instead of 5, and `seen_jobs(first_seen_at)` gained an index.
WAL is the fix that covers hand-run commands and any future reader; the
tick offset stays as belt and braces. The docstring in
`qualify/candidates.py` records both and no longer claims the read is
lock-free.

---

**A second address source for the domains a live probe cannot read.**
*(2026-09-03)*
*Why:* the unattended run's first live day spent its entire daily budget
on seven companies — Kalshi, DeepL, TripleLift, GLG, Rocket Money,
fuboTV, Industrious — every one catch-all or sitting behind Cisco
IronPort, Mimecast, or Proofpoint, and every one parked with zero drafts.
Not a bug: the keyless probe correctly refuses to guess on a domain that
accepts any address, and correctly cannot reach a domain that refuses
its connection. But it meant the residual dependency named the same day
(previous entry, item 12) showed up on 7 of 8 companies within hours of
going live, not eventually. A verifier cannot fix this — it confirms an
address already in hand, and the problem here is having none. What
answers "what is this named person's email" on a domain a probe cannot
read is a second *source*: Apollo's person-enrichment endpoint
(people/match), free tier, which returns Apollo's own sourced email for
a given name and domain rather than asking the domain's server anything.
Apollo's domain-wide roster (organization search, the thing that would
replace confirm_pattern outright) is paid-only; person-by-person
enrichment is not.
*Alternatives:* raising the daily cap (rejected — would only research
more companies that hit the identical wall); a paid Hunter tier
(rejected — Yash's standing call); waiting for the 09-11 reset
(rejected — recurs monthly, and the gap was visible in one day);
rendering the pattern from a data-broker address the way `resolve_slate`
already refuses to (not this decision — a possible future one, not
built).
*Consequence:* `_apollo_match` / `_apollo_rung` in `outreach/verify.py`,
the resolution ladder's last rung in both `resolve_address` and
`resolve_slate`, after pattern, roster and probe have all failed. Costs
one Apollo credit per company: `resolve_slate` gates it behind
`have_verified` exactly as it already gates paid mailbox verification,
so an alternate candidate is never charged once someone on the slate has
resolved. A match is trusted at `verified` without an SMTP re-check —
re-probing a catch-all domain would only return `catch_all` and discard
the one thing Apollo knew that the domain's own server cannot supply —
but still runs the same `_name_conflict` guard every other rung does,
and only a recognized `email_status` becomes an address; an unfamiliar
value degrades to a skip rather than trusting an unverified claim.
Written from the documented request/response shape and untested until a
real key exists, the same posture as the ZeroBounce and MillionVerifier
adapters. `outreach_run.py verifiers` now shows whether a key is
present; Apollo publishes no credits-remaining endpoint, so that row can
only say ready or not, not how much is left. Cost, free tier: an
Organization plan is required for the domain-wide roster; person
enrichment is credit-capped by account type, work-email signups get a
materially larger allowance than a personal one, which Yash's is.


**Correction, same day: Apollo's free plan does not include this
endpoint.** A real key, added a few hours after the entry above, hit an
immediate 403 on `people/match`: "not included in your Free plan and is
not accessible, even with a master key." Every source consulted before
building said otherwise — third-party pricing writeups, none of them
Apollo's own docs stating it in so many words. First-party beats them:
this is now confirmed, not inferred. The code itself needed no fix —
`_apollo_match`'s existing "skip on any HTTP error" path degraded
correctly, cached nothing false, and the offline test suite (name
conflicts, credit gating, malformed response shapes) still holds
regardless. What did need fixing: `provider_status` was reporting
"ready" from key presence alone, which this 403 proves is not the same
thing as the endpoint being reachable — it now makes one live probe call
and reports the plan-gate error by name. The rung stays in the ladder,
correct and inert, unless the Organization plan is ever purchased — a
paid decision, deliberately not made here.
