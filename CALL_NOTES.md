# Call notes — the engineering underneath the stories

**No agent reads this file.** That is the entire point of it existing.

`PROFILE.md` is not a reference document, it is Agent 2's prompt. Anything
sitting in it is material the drafter will reach for, and on 2026-08-24
that stopped being theoretical: three consecutive drafts explained that the
model only classifies accounts while the arithmetic stays deterministic,
each time citing the gating bullets that used to live in `PROFILE.md`.
Instructions not to use them did not work — not a soft warning, not
labelling the section "call material, not email material", not a hard ban
elsewhere in the contract. A model given detailed technical material will
write about it.

So the material moved here instead. Yash knows his own systems; he does not
need them in a prompt to discuss them on a call. What `PROFILE.md` keeps is
what is needed to *choose* a story and describe what it did for people.

Yash's diagnosis, which this file is the answer to: *"The current setup
results in the LLM almost copy and pasting its notes. You are optimizing to
include the facts given to you. When it should instead look at them from a
birdseye pov."*

---

## The LLM P&L classifier — the autonomy gating

The interesting engineering, and the thing to talk about wherever anyone is
putting an LLM near real consequences.

- **The model maps, it never does arithmetic.** Claude only decides which
  member an account belongs under. All summing, netting and sign logic is
  deterministic.
- **Operators are derived, not model-chosen.** How an account contributes
  (`+`/`-`) follows from its type and the roll-up path. Letting the model
  choose produced sign errors on real charts.
- **Constrained decoding forces completeness.** The response schema is keyed
  by account code with every code required, so it structurally cannot skip
  an account, classify one twice, or invent a code.
- **Every run reconciles.** The apex value is checked against the same
  figure computed independently from account types.
- **Low-confidence rows are isolated** into an exception report for human
  review rather than flowing through.

Batched at 50 accounts per request with recursive halving when the schema
goes over budget, streaming with backoff retry on mid-stream overloads the
SDK's own retries miss.

Validated on a full real client run: P&L net income exact to the cent,
balance sheet ties to zero.

---

## Two-queue dispatcher/executor split

Everything fans out through Celery across two queues. `fast_checks` runs
short transactional tasks that query Postgres, decide what work exists, and
enqueue one task per unit of work; it never does upstream I/O and is
idempotent, so a worker restart costs nothing. `long_jobs` runs the actual
network I/O, one task per report, at concurrency 4 because each task can sit
on a socket for minutes (Vena uploads have a 1200-second timeout).

Without the split, one slow Vena upload blocks the scheduler from noticing
the next SFTP poll.

## Per-job distributed locking

Every executor acquires a Redis lock named
`lock:<task>:<username>:<report>` with a 3600-second TTL before doing work.
Lock held means return immediately and let the next dispatcher tick retry.
At-most-one-runner per (user, report), which is what stops duplicate
financial data from being pushed downstream.

## PointClickCare — the mechanics

**mTLS and the marketplace.** Two-legged OAuth plus mutual TLS, with the
PFX certificate pulled from Azure Key Vault at runtime. Led Finaptive's
First Time App Validation and implemented the rate limiting and
error-handling safeguards that were blocking approval.

**The delay queue.** PCC fires its webhook the instant a record is created,
before nurses have finished charting, so alerts arrived with blank clinical
narratives. Fixed with an 1800-second load-bearing delay queue in Celery
that holds the webhook and then fetches the enriched record. Deployed
within 21 minutes of the request.

**The minimal handler.** The webhook route does exactly three things: check
Basic auth, enqueue, return 204. Any non-2xx makes PCC retry, so it returns
5xx only on enqueue failure, which is precisely when a retry is wanted.

## Deltek Costpoint ODBC bridge — the mechanics

A containerized Windows-side Flask service sitting next to the proprietary
ODBC driver, taking queries over HTTPS and streaming results back as CSV in
5,000-row chunks so memory stayed flat; and a Linux-side connector consuming
that stream with `ROWVERSION` as a pseudo-cursor for pagination, dynamic
incremental filters, and retry backoffs. Tables had millions of rows and
conflicting definitions of "recent" timestamps.

## Multi-tenant security — the mechanics

AES-256-GCM encryption at rest for all connector and connection secrets,
12-byte IV per encryption, wire format `base64(IV ‖ TAG ‖ CIPHERTEXT)`.
Tenant boundary enforced through company/client scoping on every query,
with an `authorize_owner_or_admin` decorator on mutation routes.
