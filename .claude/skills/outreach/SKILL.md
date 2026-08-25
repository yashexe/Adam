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

## Step 1 — find who is worth contacting

```bash
python3 outreach_run.py prepare --days 1 --json
```

This scores recent postings, keeps the best posting per company, and drops
every company that already has a pending draft or has been contacted. The
dedup is per-company and non-negotiable — never work around a skip.

Default window is 1 day. Widen with `--days 7` if the user asks for a
backlog, and lower `--min-score` only if they explicitly want to see
weaker matches.

If nothing comes back, say so and stop. That is a normal outcome on a
quiet day — weekends produce very few NY postings.

Show the user the list and how many you intend to process. Default to the
top 3 unless they say otherwise; each company costs a real agent call.

## Step 2 — find the contact (Agent 1)

For each company, invoke the `contact-finder` subagent, one call per
company, giving it the company slug, job board platform, posting title,
posting URL, location, and funding hint if present.

Its contract is in `.claude/agents/contact-finder.md`. Two things to
respect when you read its output:

- **It returns a person, not an address.** Address construction moved to
  stage 4 after it produced a confident wrong answer. If it hands you a
  constructed email anyway, ignore it and pass `observed_address` through
  instead.
- **`confidence: none` means skip the company.** Do not substitute a
  generic `careers@` or press contact. A skipped company costs nothing; a
  wrong one costs a real email to a real person.
- **Never pass a role account as `observed_address`.** That field is used
  as a fallback address to draft *to*, and Agent 1 frequently observes
  `support@` or `info@` because those are the easiest real addresses to
  find on a company site. `finalize` now refuses them outright, but omit
  them rather than relying on the guard.

## Step 3 — write the draft (Agent 2)

Invoke the `drafter` subagent. Its contract is in
`.claude/agents/drafter.md`.

**Give it only what it needs:** the contact's name and role, the posting
text and metadata, and **today's day of the week** (`date "+%A"`). It has no
tools and cannot look the date up, and it needs it to ask for a call in a
week that actually exists — "this week" written on a Friday is one day's
notice. Without it the drafter falls back to "next week". Do **not** paste résumé facts — it reads
`PROFILE.md` itself, and that file is the single place his background is
maintained. Do **not** pass Agent 1's source notes, its research trail, the
verification label, or anything about how the contact was found. That
restriction is deliberate, see `PIPELINE.md` stage 4.

## Step 4 — verify, draft, record

Pipe one JSON object per company into:

```bash
python3 outreach_run.py finalize
```

```json
{
  "candidate": { ...the object from step 1, verbatim... },
  "contact_name": "the target contact",
  "contact_role": "Head of Engineering",
  "domain": "company-a.com",
  "observed_address": "press.contact@company-a.com",
  "subject": "erp integration work at company-a",
  "body": "...Agent 2's body, paragraphs separated by blank lines...",
  "source_notes": "Agent 1's source_notes field, verbatim"
}
```

**Always include `source_notes`** — Agent 1's own field from step 2, passed
through unedited. This is the one place it goes: stored on the claim so a
pending or discarded draft can be reviewed later for *why* that contact was
chosen, without ever reaching Agent 2 (the step 3 restriction is unchanged —
this field is for the record, not the draft).

This lints the draft, resolves the real address from the domain's own
pattern, verifies it, refuses to draft if the address is undeliverable,
appends the draft to Gmail with the résumé attached, and claims the
company.

**If it exits with `lint_failed`, send the listed issues back to the
`drafter` and ask for another pass**, then finalize again. The linter
checks sentence length, clause pileups, number count and how much
implementation vocabulary the draft carries, because the drafter reliably
drifts toward dense technical prose otherwise. Two retries is plenty; if it
still fails, show the user the draft and the issues rather than forcing it
through with `ignore_lint`. It exits non-zero when no
deliverable address was found — report that plainly rather than retrying
with a guessed address.

## Step 5 — report

Tell the user what landed in Gmail Drafts, the verified address and its
label for each, and anything skipped and why. Then stop. Reviewing and
sending is theirs.

`python3 outreach_run.py status` shows pending drafts and contacted
companies at any time.

## Costs worth knowing

- Agent 1 runs 50–80k tokens and 15–30 tool calls per company.
- Stage 4 spends 2–3 Hunter credits per company against a 100/month tier.

Both are per-company, which is why dedup claims a company on first attempt
rather than re-researching it for every new posting.
