# Upstream note from ashby-ny-tracker — 2026-08-30

Written by Claude from an ashby-ny-tracker session, at Yash's request, for
later processing here. Context: this weekend the tracker shipped several
changes that alter what Adam's intake sees. **I read only this repo's .md
files, no code** — each item below names what to verify before acting.

## 1. tracker.db now contains platforms Adam may not fetch board text for

- **Lever** matches since 2026-08-29 (platform `lever`; posting API
  `api.lever.co/v0/postings/<slug>?mode=json`, bare JSON array, title in
  `text`, epoch-ms `createdAt`).
- **Workable** matches since 2026-08-29 (platform `workable`). These do
  NOT come from per-company boards: Workable's widget API
  (apply.workable.com) rate-limits hard (~25 req/s burst → ~15-min IP-wide
  429 lockout, measured) and has no ETags, so the tracker walks Workable's
  cross-customer search feed (`jobs.workable.com/api/v1/jobs`) instead.
  Full posting descriptions ARE available in that feed's job objects
  (`description`, `requirementsSection`, `benefitsSection`) and via
  `jobs.workable.com/api/v1/jobs/<shortcode>`; avoid the widget API for
  bulk fetches from any IP you care about.

Adam's decisions table says "Ashby/Greenhouse stay the only discovery
sources for now" and the roadmap describes fetching board text for the
judge — **verify whether `qualify/boards.py` handles `lever` and
`workable` rows**, and decide whether to fetch their text or skip those
platforms explicitly. Silent unjudgeable rows would be the bad outcome.

## 2. Intake volume roughly doubled on 2026-08-30

The tracker's location filter was widened from NY-only to NY **or
explicitly-US remote** (both a `remote` word and a US marker required;
bare "Remote" still excluded). Measured on the prior week: ~940 NY+eng
postings/week → ~1,670/week under the widened filter. Workable adds ~34/wk
on top. Consequences for Adam: bigger judge windows per `qualify_run.py`
sweep, and a new class of matches (US-remote at companies with no NY
presence) that may deserve different treatment in contact strategy or
tiering — that's a policy question for Yash, not a bug.

## 3. A stale fact in this repo's roadmap, in Adam's favor

`docs/roadmap.md` says the Pi runs Python 3.7.3. Since the tracker's
pyenv move it runs **Python 3.11** (`/home/pi/ashby-ny-tracker/.venv`).
The "offload the deterministic half to the Pi" roadmap item has less
porting friction than documented. (The Pi still cannot run the `claude`
binary — armv7l/Node constraint unchanged.)

## Proposed for Adam: quick-apply vs writeup classification (Yash-approved placement)

Yash's rule, stated 2026-08-30: **if an application form has open-ended
questions, automation must not touch that application at all** — he
handles it fully by hand. Autofill tooling (extension TBD, separate
decision) is only ever invoked on forms pre-verified to contain nothing
open-ended. He proposed this classification live downstream in Adam,
post-judge, rather than in the tracker — agreed, because only above-bar
postings need classifying (~10x fewer fetches) and the tracker stays
frozen.

Design sketch (verify endpoints against code before building):

- **Greenhouse** (documented public API):
  `GET boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}?questions=true`
  returns every application question with field types (`input_text`,
  `input_file`, `multi_value_single_select`, `textarea`, ...). Verified
  live 2026-08-30 on a Stripe posting (16 questions, all mechanical).
- **Ashby** (internal SPA endpoint, no stability promise — MUST fail
  open): `POST jobs.ashbyhq.com/api/non-user-graphql`, op
  `ApiJobPosting`, same call every visitor's browser makes. Response
  carries `jobPosting.applicationForm.sections[].fieldEntries[].field.type`
  (`String`, `File`, selects, a long-text type for essays). Verified live
  2026-08-30 on a Ramp posting. Capture the exact query body from the
  apply page's network tab when implementing.
- **Classifier**: any long-text/textarea-type field → `writeup`; else
  `quick_apply`. Fetch failure or unrecognized shape → no tag (unknown =
  treated as writeup = manual, the conservative direction of Yash's rule).
- **Lever / Workable**: no public form schema; always untagged → manual.
- **Placement**: deterministic enrichment after the judge, only for
  postings above the spend bar; surface in `qualify_run.py` output and
  the review UI next to the score. One fetch per above-bar posting —
  negligible volume, same fail-open ethos as the rest of QUALIFY.

## Unchanged upstream facts worth knowing

- Tracker detection latency is ~3 min median (p90 6 min), cron `*/5`,
  cycle ~30s across 11,286 boards + the Workable feed.
- `seen_jobs`/`pending_alerts` schemas unchanged; the widening only
  changes which postings qualify, not row shape.
- HN "Who is hiring?" as a discovery source was measured 2026-08-30 and
  rejected (87% of its board links already known; 11 new companies over 3
  months, 0 alert-worthy postings) — don't re-propose it here either.
