"""
Stage 8 — outreach state and the dedup log.

A local SQLite file, not ashby-ny-tracker's `tracker.db`. The original
design put these tables in the tracker's database; that decision was made
before the two-host reality was in view, and its stated rationale was
"reuse what exists, no new infrastructure". Once the live DB turned out to
sit on the Pi and this pipeline on the Mac, same-file stopped being the
cheap option: it would mean cross-host writes, a WAL migration on a live
database, and a second writer on a file whose owner assumes it is alone for
the 4-7 minutes of every poll cycle. The Pi stays strictly read-only.
See docs/decisions.md.

The invariant this file exists to enforce: **one outreach attempt per
company, ever.** Not per posting, not per contact. `outreach_log` is keyed
on `company_slug` alone so the database itself refuses a second send rather
than relying on a caller to check first.

Draft bodies are deliberately not stored. They live in Gmail once stage 6
appends them, and a copy here would only drift from whatever was actually
edited before sending.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "outreach.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_outreach (
    company_slug    TEXT PRIMARY KEY,
    platform        TEXT NOT NULL,
    job_id          TEXT NOT NULL,
    job_title       TEXT,
    job_url         TEXT,
    score           INTEGER,
    contact_name    TEXT,
    contact_role    TEXT,
    contact_email   TEXT,
    confidence      TEXT,
    draft_subject   TEXT,
    -- A higher-scoring posting that appeared after the draft was written.
    -- Recorded, never swapped in: see update_posting().
    superseded_note TEXT,
    status          TEXT NOT NULL DEFAULT 'drafted',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- PRIMARY KEY is company_slug alone. This is the invariant, expressed
-- where it cannot be bypassed: a second send to the same company fails at
-- the database, not at a caller's if-statement.
CREATE TABLE IF NOT EXISTS outreach_log (
    company_slug    TEXT PRIMARY KEY,
    contact_email   TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    sent_at         TEXT NOT NULL DEFAULT (datetime('now')),
    -- Copied from the claim at send time rather than joined back to
    -- pending_outreach, so the log stays answerable on its own: the whole
    -- point of recording the role is to compare reply rates across contact
    -- types, and that analysis must survive a pending row being cleaned up.
    contact_role    TEXT,
    -- Reply tracking. Until 2026-08-24 nothing recorded what came back, so
    -- "which kind of contact actually responds" was unanswerable and every
    -- argument about targeting stayed a matter of opinion.
    replied_at      TEXT,
    reply_checked_at TEXT
);
"""

# CREATE TABLE IF NOT EXISTS cannot add columns to a table that already
# exists, so databases created before reply tracking need these bolted on.
# Idempotent, runs on every connect.
_ADDED_COLUMNS = {
    "outreach_log": ("contact_role", "replied_at", "reply_checked_at"),
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for column in columns:
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
    conn.commit()


class AlreadyClaimed(Exception):
    """Raised when a company has a pending draft or has already been sent to."""


# A discarded draft is not a third permanent state alongside 'drafted' and
# 'sent' -- it means nothing was ever sent, so the company is not actually
# closed out. is_claimed() and record_draft() treat it as available; only
# outreach_log (a 'sent' claim) is forever.
DISCARDED = "discarded"


@dataclass
class Claim:
    company_slug: str
    contact_email: str
    job_title: str | None
    score: int | None
    status: str


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _ensure_columns(conn)
    conn.commit()
    return conn


def claim_state(company_slug: str) -> tuple[str | None, sqlite3.Row | None]:
    """('sent'|'drafted'|None, row). The single question the pipeline asks
    before spending an Agent 1 call."""
    with connect() as conn:
        sent = conn.execute(
            "SELECT * FROM outreach_log WHERE company_slug = ?", (company_slug,)
        ).fetchone()
        if sent:
            return "sent", sent
        pending = conn.execute(
            "SELECT * FROM pending_outreach WHERE company_slug = ?", (company_slug,)
        ).fetchone()
        if pending:
            return pending["status"], pending
    return None, None


def is_claimed(company_slug: str) -> bool:
    state, _ = claim_state(company_slug)
    return state is not None and state != DISCARDED


def record_draft(
    *,
    company_slug: str,
    platform: str,
    job_id: str,
    job_title: str | None = None,
    job_url: str | None = None,
    score: int | None = None,
    contact_name: str | None = None,
    contact_role: str | None = None,
    contact_email: str,
    confidence: str | None = None,
    draft_subject: str | None = None,
) -> None:
    """Claim a company. Raises AlreadyClaimed if it is spoken for.

    A prior `discarded` row does not block this -- that status means the
    earlier draft was deleted before ever being sent, so `INSERT OR
    REPLACE` overwrites it with the new attempt (and resets status back
    to the 'drafted' default, since that column is not in the VALUES
    list below).
    """
    state, existing = claim_state(company_slug)
    if state == "sent":
        raise AlreadyClaimed(
            f"{company_slug} was already contacted at {existing['contact_email']} "
            f"on {existing['sent_at']} — re-contacting is a manual decision"
        )
    if state is not None and state != DISCARDED:
        raise AlreadyClaimed(
            f"{company_slug} already has a {state} draft to {existing['contact_email']}; "
            f"use update_posting() to point it at a better posting instead"
        )

    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pending_outreach (company_slug, platform, job_id, "
            "job_title, job_url, score, contact_name, contact_role, contact_email, "
            "confidence, draft_subject) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (company_slug, platform, job_id, job_title, job_url, score, contact_name,
             contact_role, contact_email, confidence, draft_subject),
        )
        conn.commit()


def update_posting(
    *, company_slug: str, platform: str, job_id: str, job_title: str | None,
    job_url: str | None, score: int | None,
) -> bool:
    """Note that a higher-scoring posting appeared. Does **not** swap it in.

    PIPELINE.md's rate-limiting policy originally said the pending draft
    should be repointed at the better posting. That was written before
    drafts lived in Gmail, and it does not survive the move: repointing
    changes this row but not the email, which was written against the old
    posting and may since have been edited by hand. The record would then
    describe a draft that does not exist.

    It also trusts the score as a proxy for "better", which QUALIFY has not
    earned — on 2026-08-23 this swapped company-a's claim from "Software
    Engineer" (90) to "Software Engineering Intern" (95), because the gate
    ranks an intern req above a real engineering role while semantic_fit is
    unscored.

    So the better posting is recorded for the human reviewing the draft,
    and the claim keeps pointing at what the draft actually says. Returns
    True if a note was written.
    """
    state, existing = claim_state(company_slug)
    if state != "drafted":
        return False
    if score is not None and existing["score"] is not None and score <= existing["score"]:
        return False

    note = f"higher-scoring posting seen {job_title!r} (score {score}, {job_id})"
    with connect() as conn:
        conn.execute(
            "UPDATE pending_outreach SET superseded_note=?, updated_at=datetime('now') "
            "WHERE company_slug=?",
            (note, company_slug),
        )
        conn.commit()
    return True


def discard_draft(company_slug: str) -> sqlite3.Row:
    """Release a claim whose draft was deleted from Gmail before ever being
    sent. Unlike mark_sent, this does not close the company out
    permanently -- record_draft() will accept a fresh attempt at it.

    This only updates the claim; trashing the actual Gmail draft is the
    caller's job (see outreach.gmail_draft.trash_draft), since a draft the
    human already deleted by hand has nothing left to trash.
    """
    state, existing = claim_state(company_slug)
    if state is None:
        raise ValueError(f"{company_slug} has no draft to discard")
    if state == "sent":
        raise AlreadyClaimed(
            f"{company_slug} was already contacted at {existing['contact_email']} "
            f"on {existing['sent_at']} — cannot discard a sent outreach"
        )
    with connect() as conn:
        conn.execute(
            "UPDATE pending_outreach SET status=?, updated_at=datetime('now') "
            "WHERE company_slug=?",
            (DISCARDED, company_slug),
        )
        conn.commit()
    return existing


def mark_sent(*, company_slug: str, contact_email: str, outcome: str = "sent") -> None:
    """Record that outreach went out. After this the company is closed.

    The contact's role is copied across from the claim, because reply rates
    are only interesting broken down by *who* was emailed.
    """
    _, claim = claim_state(company_slug)
    role = claim["contact_role"] if claim and "contact_role" in claim.keys() else None
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO outreach_log "
            "(company_slug, contact_email, outcome, contact_role) VALUES (?,?,?,?)",
            (company_slug, contact_email, outcome, role),
        )
        conn.execute(
            "UPDATE pending_outreach SET status='sent', updated_at=datetime('now') "
            "WHERE company_slug=?",
            (company_slug,),
        )
        conn.commit()


def record_reply_check(
    company_slug: str, *, replied_at: str | None, checked_at: str
) -> None:
    """Store the outcome of looking for a reply from this company's contact.

    `checked_at` is always written, `replied_at` only when one was found, so
    "checked and heard nothing" is distinguishable from "never checked" —
    the difference between a real silence and an unknown.
    """
    with connect() as conn:
        if replied_at:
            conn.execute(
                "UPDATE outreach_log SET replied_at=?, reply_checked_at=? "
                "WHERE company_slug=?",
                (replied_at, checked_at, company_slug),
            )
        else:
            conn.execute(
                "UPDATE outreach_log SET reply_checked_at=? WHERE company_slug=?",
                (checked_at, company_slug),
            )
        conn.commit()


def reply_rates() -> list[sqlite3.Row]:
    """Replies broken down by contact role — the numbers that settle
    arguments about who is worth emailing. Meaningless until there are
    enough rows; reported honestly rather than smoothed."""
    with connect() as conn:
        return conn.execute(
            "SELECT COALESCE(NULLIF(TRIM(contact_role), ''), '(role not recorded)') "
            "         AS role, "
            "       COUNT(*) AS sent, "
            "       SUM(CASE WHEN replied_at IS NOT NULL THEN 1 ELSE 0 END) AS replied, "
            "       SUM(CASE WHEN reply_checked_at IS NULL THEN 1 ELSE 0 END) AS unchecked "
            "FROM outreach_log GROUP BY role ORDER BY sent DESC"
        ).fetchall()


def pending() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM pending_outreach WHERE status='drafted' ORDER BY score DESC"
        ).fetchall()


def contacted() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM outreach_log ORDER BY sent_at DESC"
        ).fetchall()


def discarded() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM pending_outreach WHERE status=? ORDER BY updated_at DESC",
            (DISCARDED,),
        ).fetchall()
