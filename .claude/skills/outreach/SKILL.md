---
name: outreach
description: Run the cold-outreach pipeline over recent ashby-ny-tracker matches — score them, find a hiring contact, verify the address, draft the email, and leave it in Gmail Drafts for review. Use when the user asks to run outreach, check for new outreach candidates, or draft outreach for a specific company.
---

# Outreach pipeline

Turns fresh NY job postings into personalized cold emails sitting in Gmail
Drafts, ready for a human to read and send.

You drive stages 3 and 5 — the two agent calls. Everything else is
deterministic code in `outreach_run.py`, and you should not reimplement any
of it inline. Full architecture: `PIPELINE.md`.

## The one rule

**Nothing you do sends an email.** There is no send path in this project.
Drafts land in Gmail and a human presses Send. Never offer to send, never
look for a way to send, and never treat a user's "yes, that looks good" as
authorization to do anything beyond leaving the draft where it is.

## Step 1 — judge, then find who is worth contacting

The `relevance-judge` subagent's 0-100 **is** the QUALIFY score — there
is no composite around it (docs/qualify.md, "The judge becomes the
score"). An unjudged posting has no score at all: `prepare` will not
rank it, only count it in `skipped` as unjudged. **Always judge the
window before calling `prepare`** — that is what gives `prepare`
something to rank.

Judge output includes calibration anchors: `judge-save` checks them
against known bands and prints a WARNING to stderr if the judge has
drifted. Surface that warning to the user instead of proceeding
silently — drifted scores mean the whole batch deserves a human glance
before any spend.

```bash
python3 outreach_run.py judge --days 1
```

If it prints `all N posting(s) in the window are already judged` to
stderr, skip straight to `prepare` below — this makes re-running the
pipeline free, since judgements are cached per posting, not per run.

Otherwise it prints a batch of at most 40 postings to stdout. Invoke the
`relevance-judge` subagent with that batch verbatim as its input (it reads
`PROFILE.md` itself). Take its JSON array output and pipe it into:

```bash
python3 outreach_run.py judge-save
```

If judge's stderr said more postings remain in the window (common since
the tracker added Lever/Workable and US-remote matches), **loop** —
`judge` again, another subagent call, `judge-save` again — until it
prints `already judged`. Never paste multiple batches into one subagent
call; oversized single batches have failed outright.

Then:

```bash
python3 outreach_run.py prepare --days 1 --json
```

This scores recent postings, keeps the best posting per company, and drops
every company that already has a pending draft or has been contacted. The
dedup is per-company and non-negotiable — never work around a skip.

Default window is 1 day. Widen with `--days 7` if the user asks for a
backlog, and lower `--min-score` only if they explicitly want to see
weaker matches. Judge the same window before preparing it, whatever it is.

If nothing comes back, say so and stop. That is a normal outcome on a
quiet day — weekends produce very few NY postings.

Show the user the list and how many you intend to process. Default to the
top 3 unless they say otherwise; each company costs a real agent call.

## Step 2 — find the contact slate (Agent 1)

For each company, invoke the `contact-finder` subagent, one call per
company, giving it the company slug, job board platform, posting title,
posting URL, location, and funding hint if present.

Its contract is in `.claude/agents/contact-finder.md`. It returns a
**ranked slate of up to three candidates**, each with evidence and its own
confidence, plus the domain and an optional `personalization_context`.
Things to respect when you read its output:

- **It returns people, not addresses.** Address construction moved to
  stage 4 after it produced a confident wrong answer. If it hands you a
  constructed email anyway, ignore it and pass `observed_address` through
  instead.
- **An empty `candidates` array means skip the company** — but surface the
  skip to the user with the source_notes before moving on, rather than
  silently writing the company off. Do not substitute a generic `careers@`
  or press contact. A skipped company costs nothing; a wrong one costs a
  real email to a real person.
- **Never pass a role account as `observed_address`.** That field is used
  as a fallback address to draft *to*, and Agent 1 frequently observes
  `support@` or `info@` because those are the easiest real addresses to
  find on a company site. `finalize` now refuses them outright, but omit
  them rather than relying on the guard.

## Step 3 — resolve the slate, then let the human pick

Pipe the slate into:

```bash
python3 outreach_run.py verify-slate
```

```json
{
  "domain": "company-a.com",
  "candidates": [ ...Agent 1's candidates array, verbatim... ]
}
```

It returns each candidate with the address that would be used, where the
address came from (the domain's own pattern, or Hunter's roster), and a
verification verdict — one domain lookup for the whole slate, credits
spent only until the first deliverable candidate.

**Show the user the resolved slate and let them choose** — name, role,
evidence, confidence, address, and verification label per candidate,
plus the one-line reason when a candidate has no reachable address.
Default to rank #1 when it resolved cleanly, but the pick is theirs: the
slate exists precisely so the selection is a reviewable human decision
instead of a committed agent guess. If #1 has no deliverable address and
an alternate does, say so plainly — that exact situation used to burn the
whole company.

Worth offering while they look: the posting's LinkedIn page sometimes
names the actual hiring team, visible only logged-in — a ten-second glance
in their own browser, never something this pipeline automates.

## Step 4 — write the draft (Agent 2)

Invoke the `drafter` subagent. Its contract is in
`.claude/agents/drafter.md`.

**Give it only what it needs:** the chosen contact's name and role, the
posting text and metadata, Agent 1's `personalization_context` if it
returned one (the whole digest, verbatim — the drafter chooses which
facts earn a sentence and drops the rest), and
**today's day of the week** (`date "+%A"`). It has no tools and cannot
look the date up, and it needs it to ask for a call in a week that
actually exists — "this week" written on a Friday is one day's notice.
Without it the drafter falls back to "next week". Do **not** paste résumé
facts — it reads `PROFILE.md` itself, and that file is the single place
his background is maintained. Do **not** pass Agent 1's source notes, its
research trail, per-candidate evidence, the verification label, or
anything about how the contact was found. `personalization_context` is
the one deliberate window in that wall — it carries what the company said
publicly, not how the contact was identified.

## Step 4.5 — LinkedIn drafts, by score bar

LinkedIn is a second, human-pasted channel: nothing here automates any
LinkedIn action (their ToS bans it and the one rule generalizes — Adam
writes, Yash pastes). Which pieces a company earns depends on its judge
score:

- **Score ≥ 85**: connection note + post-accept DM + **InMail**
  (subject + body). InMail credits are a limited monthly Premium
  resource; this bar matches the band where all of his real interviews
  happened, so the scarce credits go where conversion is proven.
- **Score 70–84**: connection note + post-accept DM only.
- **Below 70**: email only, no LinkedIn drafts.

For each qualifying company, invoke the `drafter` again in **LinkedIn
mode** (see its contract): say it is a LinkedIn request, pass the
contact's name and role, the posting title, the finished email body, and
whether InMail is requested (score ≥ 85). Include the result in the
finalize payload as:

```json
"linkedin": {
  "connection_note": "...",
  "post_accept_dm": "...",
  "inmail_subject": "... (>=85 only)",
  "inmail_body": "... (>=85 only)"
}
```

`finalize` lints these too (the 300-char note cap is a platform limit, so
an over-cap note fails the whole finalize — send it back to the drafter
like any lint failure). They are stored on the claim and rendered as
copy-paste blocks in the review UI.

## Step 5 — verify, draft, record

Pipe one JSON object per company into:

```bash
python3 outreach_run.py finalize
```

```json
{
  "candidate": { ...the object from step 1, verbatim... },
  "contact_name": "the chosen candidate's name",
  "contact_role": "Head of Engineering",
  "domain": "company-a.com",
  "observed_address": "press.contact@company-a.com",
  "subject": "erp integration work at company-a",
  "body": "...Agent 2's body, paragraphs separated by blank lines...",
  "source_notes": "Agent 1's source_notes field, verbatim",
  "contact_slate": [ ...the resolved slate from step 3, verbatim... ]
}
```

**Always include `source_notes` and `contact_slate`** — passed through
unedited. This is the one place they go: stored on the claim so a pending
or discarded draft can be reviewed later for *why* that contact was
chosen and *who else* was considered, without ever reaching Agent 2 (the
step 4 restriction is unchanged — these fields are for the record, not
the draft). Finalize re-verifies the chosen contact's address itself; the
step 3 resolution was advisory.

This lints the draft, resolves the real address from the domain's own
pattern (or Hunter's roster), verifies it, refuses to draft if the address
is undeliverable, appends the draft to Gmail with the résumé attached, and
claims the company.

**If it exits with `lint_failed`, send the listed issues back to the
`drafter` and ask for another pass**, then finalize again. The linter
checks sentence length, clause pileups, number count and how much
implementation vocabulary the draft carries, because the drafter reliably
drifts toward dense technical prose otherwise. Two retries is plenty; if it
still fails, show the user the draft and the issues rather than forcing it
through with `ignore_lint`. It exits non-zero when no
deliverable address was found — report that plainly rather than retrying
with a guessed address.

## Step 6 — report

Tell the user what landed in Gmail Drafts, the verified address and its
label for each, and anything skipped and why. When a label is `catch_all`
or `risky`, spell out what that means — the domain accepts anything, so
nothing confirmed this particular mailbox — and that sending anyway,
switching to an alternate from the slate, or discarding are all theirs to
choose. Then stop. Reviewing and sending is theirs.

`python3 outreach_run.py status` shows pending drafts and contacted
companies at any time.

## Step 7 — follow-ups (when asked, or at the end of a run)

One polite bump per company, ever, is policy since 2026-08-26
(`docs/decisions.md`); silence after that is an answer. Check who is due:

```bash
python3 outreach_run.py bumps
```

It checks Gmail live for replies first (recording what it finds), then
classifies every contacted company: `eligible` means confirmed silence for
5–15 business days. For each eligible company **the user wants bumped** —
ask, per company, never in bulk — invoke the `drafter` subagent in bump
mode: say it is a bump, and give it the contact's first name, the role
title, and the business days elapsed, all from the `bumps` listing. The
original email's body is deliberately not available (the store keeps no
bodies) and a bump never restates it anyway. Expect body only, two
sentences. Then:

```bash
python3 outreach_run.py bump <company>    # body on stdin
```

The bump lands as a reply draft in the original Gmail thread, résumé
deliberately not re-attached, and the human sends it from Gmail like
everything else. `bump` re-checks everything itself before drafting —
live silence against Gmail, the one-bump cap, the window floor, and the
same lint every draft passes — so a refusal from it is a fact, not an
obstacle: report it, never work around it. `replied`, `bounced`,
`too_soon`, `stale`, and `bumped` classifications are reported, not acted
on — a reply is answered by a human, a bounce means the address was
wrong, and a stale thread is only worth reopening with genuinely new
information.

## Costs worth knowing

- The judge is one batched call over the whole unjudged window, not one
  per company — cheap regardless of how many postings are in it, and free
  on every subsequent run of the same window since judgements are cached.
- Agent 1 runs 50–80k tokens and 15–30 tool calls per company.
- Hunter: one domain-search per company (cached per domain, shared by
  verify-slate and finalize). Since 2026-08-29 that roster lookup is the
  only thing Hunter's free credits are reserved for — mailbox probes walk
  a provider chain first (ZeroBounce, MillionVerifier, each active when
  its key is in `.env`; Hunter last), so a company typically costs 1
  Hunter credit plus 1–2 probes at whichever provider has quota.
  verify-slate stops spending at the first deliverable candidate, and
  finalize's re-verification of the chosen address is a cache hit. If
  every provider is exhausted, drafts land labeled `unverified` — say so
  plainly in the report rather than treating it as verified.

Both are per-company, which is why dedup claims a company on first attempt
rather than re-researching it for every new posting.
