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
    -- Agent 1's reasoning: which sources it used and what stayed
    -- uncertain. Discarded until 2026-08-24, which meant a draft could
    -- never be reviewed for *why* that contact was chosen.
    source_notes    TEXT,
    -- The full ranked slate Agent 1 returned (JSON array), so a draft can
    -- be reviewed against the alternatives that were NOT chosen — the
    -- selection is a human-visible decision since 2026-08-26, not a
    -- single committed pick (docs/research/contact-strategy-findings.md).
    contact_slate   TEXT,
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
    reply_checked_at TEXT,
    -- When the one permitted follow-up bump was drafted. NULL means it is
    -- still available. Set at bump-draft creation, not at send: one bump
    -- per company is the whole policy, so "a bump was prepared" is the
    -- fact that must never happen twice (docs/decisions.md, follow-up).
    follow_up_at    TEXT
);

-- A researched company that is not a draft. Since 2026-09-03 the
-- unattended run researches and resolves a slate and then either drafts
-- to rank one (clean address, score >= 70) or parks the company here for
-- a human pick. Keyed on company_slug so prepare can skip it: without this
-- row, every run would re-research the same company. Statuses: awaiting
-- (needs a pick), approved (pick recorded, the next run drafts it),
-- drafted (a claim exists in pending_outreach), dismissed (the human said
-- no to this posting; a higher-scoring posting later re-opens it).
CREATE TABLE IF NOT EXISTS slates (
    company_slug        TEXT PRIMARY KEY,
    platform            TEXT NOT NULL,
    job_id              TEXT NOT NULL,
    job_title           TEXT,
    job_url             TEXT,
    score               INTEGER,
    domain              TEXT,
    slate_json          TEXT NOT NULL,
    resolved_json       TEXT,
    observed_address    TEXT,
    source_notes        TEXT,
    personalization_json TEXT,
    status              TEXT NOT NULL DEFAULT 'awaiting',
    chosen_name         TEXT,
    reason              TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Slugs that are not companies: a recruiting marketplace with hundreds of
-- postings under one slug, a staffing agency. Not a fit filter (fit is the
-- judge's job and stays broad) -- a note that there is nobody to email.
-- Added by a human, by hand, with a reason; prepare skips them and says so.
CREATE TABLE IF NOT EXISTS company_ignore (
    company_slug    TEXT PRIMARY KEY,
    reason          TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# CREATE TABLE IF NOT EXISTS cannot add columns to a table that already
# exists, so databases created before reply tracking need these bolted on.
# Idempotent, runs on every connect.
_ADDED_COLUMNS = {
    "outreach_log": ("contact_role", "replied_at", "reply_checked_at",
                     "follow_up_at"),
    "pending_outreach": ("source_notes", "contact_slate", "linkedin_json"),
    # The posting text at research time, so an approved pick can still be
    # drafted after the posting closes on its board (days can pass).
    "slates": ("description_text",),
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
    source_notes: str | None = None,
    contact_slate: str | None = None,
    linkedin_json: str | None = None,
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
            "confidence, draft_subject, source_notes, contact_slate, linkedin_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (company_slug, platform, job_id, job_title, job_url, score, contact_name,
             contact_role, contact_email, confidence, draft_subject, source_notes,
             contact_slate, linkedin_json),
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


def mark_sent(
    *,
    company_slug: str,
    contact_email: str,
    outcome: str = "sent",
    sent_at: str | None = None,
) -> None:
    """Record that outreach went out. After this the company is closed.

    The contact's role is copied across from the claim, because reply rates
    are only interesting broken down by *who* was emailed.

    `sent_at` lets reconciliation record when the send actually happened
    (Gmail's Date header) instead of when this row was written. The two
    diverged by three weeks once — company-a was hand-sent 2026-08-04 and
    recorded 2026-08-24 — and the follow-up window math reads this column,
    so the row-creation default is only right for sends the pipeline
    witnesses as they happen.
    """
    _, claim = claim_state(company_slug)
    role = claim["contact_role"] if claim and "contact_role" in claim.keys() else None
    with connect() as conn:
        if sent_at:
            conn.execute(
                "INSERT OR IGNORE INTO outreach_log "
                "(company_slug, contact_email, outcome, contact_role, sent_at) "
                "VALUES (?,?,?,?,?)",
                (company_slug, contact_email, outcome, role, sent_at),
            )
        else:
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


def claim_row(company_slug: str) -> sqlite3.Row | None:
    """The pending_outreach row regardless of status.

    The claim record (job title, contact name, slate) survives the move to
    'sent', and the follow-up flow reads it for context the log row
    deliberately does not carry — `claim_state` is not usable for this
    because it returns the log row once a company is sent.
    """
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM pending_outreach WHERE company_slug = ?",
            (company_slug,),
        ).fetchone()


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


def record_follow_up(company_slug: str) -> None:
    """Mark that the one permitted follow-up bump was drafted.

    Refuses a second bump and refuses to bump a company that already
    replied — both at the store, not the caller, for the same reason the
    dedup key lives in the schema.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT follow_up_at, replied_at FROM outreach_log "
            "WHERE company_slug = ?",
            (company_slug,),
        ).fetchone()
        if row is None:
            raise ValueError(f"{company_slug} was never contacted; nothing to bump")
        if row["follow_up_at"]:
            raise AlreadyClaimed(
                f"{company_slug} already got its one follow-up on "
                f"{row['follow_up_at']} — there is no second bump"
            )
        if row["replied_at"]:
            raise AlreadyClaimed(
                f"{company_slug} replied on {row['replied_at']} — a reply is "
                f"answered by a human, never bumped"
            )
        conn.execute(
            "UPDATE outreach_log SET follow_up_at=datetime('now') "
            "WHERE company_slug=?",
            (company_slug,),
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


# ── Slates awaiting a pick, and slugs that are not companies ─────────────
# Added 2026-09-03 for the unattended run (docs/decisions.md). Every
# transition is a plain UPDATE on the slug; nothing here sends or drafts.

SLATE_AWAITING = "awaiting"
SLATE_APPROVED = "approved"
SLATE_DRAFTED = "drafted"
SLATE_DISMISSED = "dismissed"


def save_slate(
    *, company_slug: str, platform: str, job_id: str, job_title: str | None,
    job_url: str | None, score: int | None, domain: str | None,
    slate_json: str, resolved_json: str | None, observed_address: str | None,
    source_notes: str | None, personalization_json: str | None,
    status: str = SLATE_AWAITING, reason: str | None = None,
    description_text: str | None = None,
) -> None:
    """Record a researched, resolved slate. Replaces any earlier row for the
    company: a slate is the latest research, not a history."""
    conn = connect()
    conn.execute(
        """INSERT INTO slates (company_slug, platform, job_id, job_title, job_url,
                score, domain, slate_json, resolved_json, observed_address,
                source_notes, personalization_json, status, reason, description_text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(company_slug) DO UPDATE SET
                platform=excluded.platform, job_id=excluded.job_id,
                job_title=excluded.job_title, job_url=excluded.job_url,
                score=excluded.score, domain=excluded.domain,
                slate_json=excluded.slate_json, resolved_json=excluded.resolved_json,
                observed_address=excluded.observed_address,
                source_notes=excluded.source_notes,
                personalization_json=excluded.personalization_json,
                status=excluded.status, reason=excluded.reason,
                description_text=excluded.description_text,
                chosen_name=NULL, updated_at=datetime('now')""",
        (company_slug, platform, job_id, job_title, job_url, score, domain,
         slate_json, resolved_json, observed_address, source_notes,
         personalization_json, status, reason, description_text),
    )
    conn.commit()
    conn.close()


def slate_row(company_slug: str) -> sqlite3.Row | None:
    conn = connect()
    row = conn.execute("SELECT * FROM slates WHERE company_slug = ?", (company_slug,)).fetchone()
    conn.close()
    return row


def slates(status: str | None = None) -> list[sqlite3.Row]:
    conn = connect()
    if status:
        rows = conn.execute("SELECT * FROM slates WHERE status = ? ORDER BY score DESC, created_at",
                            (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM slates ORDER BY status, score DESC, created_at").fetchall()
    conn.close()
    return rows


def _set_slate(company_slug: str, status: str, *, chosen_name: str | None = None,
               reason: str | None = None) -> sqlite3.Row:
    conn = connect()
    row = conn.execute("SELECT * FROM slates WHERE company_slug = ?", (company_slug,)).fetchone()
    if row is None:
        conn.close()
        raise KeyError(f"no slate for {company_slug}")
    conn.execute(
        "UPDATE slates SET status = ?, chosen_name = COALESCE(?, chosen_name), "
        "reason = COALESCE(?, reason), updated_at = datetime('now') WHERE company_slug = ?",
        (status, chosen_name, reason, company_slug),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM slates WHERE company_slug = ?", (company_slug,)).fetchone()
    conn.close()
    return row


def approve_slate(company_slug: str, chosen_name: str) -> sqlite3.Row:
    """The human picked. The next run drafts to this person, no research."""
    return _set_slate(company_slug, SLATE_APPROVED, chosen_name=chosen_name)


def dismiss_slate(company_slug: str, reason: str | None = None) -> sqlite3.Row:
    return _set_slate(company_slug, SLATE_DISMISSED, reason=reason)


def mark_slate_drafted(company_slug: str) -> None:
    """Called by finalize when a claim lands; harmless if no slate exists."""
    conn = connect()
    conn.execute("UPDATE slates SET status = ?, updated_at = datetime('now') "
                 "WHERE company_slug = ?", (SLATE_DRAFTED, company_slug))
    conn.commit()
    conn.close()


def delete_slate(company_slug: str) -> None:
    conn = connect()
    conn.execute("DELETE FROM slates WHERE company_slug = ?", (company_slug,))
    conn.commit()
    conn.close()


def ignore_company(company_slug: str, reason: str) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO company_ignore (company_slug, reason) VALUES (?, ?) "
        "ON CONFLICT(company_slug) DO UPDATE SET reason = excluded.reason",
        (company_slug, reason),
    )
    conn.commit()
    conn.close()


def unignore_company(company_slug: str) -> None:
    conn = connect()
    conn.execute("DELETE FROM company_ignore WHERE company_slug = ?", (company_slug,))
    conn.commit()
    conn.close()


def ignored() -> dict[str, str]:
    """slug -> reason."""
    conn = connect()
    rows = conn.execute("SELECT company_slug, reason FROM company_ignore").fetchall()
    conn.close()
    return {r["company_slug"]: r["reason"] for r in rows}
