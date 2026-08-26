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
