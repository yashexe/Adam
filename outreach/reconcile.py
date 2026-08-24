"""
Reconcile the claim store against Gmail.

`outreach.db` records what this pipeline *intended*: it writes a claim the
moment stage 6 appends a draft, and nothing ever checks back. Gmail records
what actually *happened*, because every consequential action after stage 6
is taken by a human in a mail client the pipeline cannot observe. The two
drift the moment the human does anything, and on 2026-08-24 both drift
directions were live at once:

- Four drafts had been deleted by hand. The store still called all four
  pending, so `prepare` kept skipping those companies for drafts that no
  longer existed.
- The company-b draft had actually been *sent* (08:00 that morning). The
  store still called it pending, so `outreach_log` -- the table whose whole
  job is "one outreach per company, ever" -- had no record of it.

The second is the dangerous one. A sent-but-unlogged company looks exactly
like an abandoned draft, so a discard would release the claim and let the
pipeline write a second cold email to someone who already got one.

So: read Gmail, do not trust the store. This module is strictly read-only
and answers one question per claim -- is the draft still sitting there, did
it go out, or is it gone? Deciding what to *do* about each answer stays
with the human, in the UI.
"""

from __future__ import annotations

import email
import imaplib
import os
from dataclasses import dataclass
from email.message import Message
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path("/Users/yashbhavsar/Code/job_search_automation/.env")
IMAP_HOST = "imap.gmail.com"
DRAFTS_MAILBOX = '"[Gmail]/Drafts"'
SENT_MAILBOX = '"[Gmail]/Sent Mail"'

# What Gmail says about a claim the store calls pending.
IN_DRAFTS = "draft"      # still there, still awaiting review
SENT = "sent"            # the human sent it; the store needs to catch up
MISSING = "missing"      # deleted by hand; the claim is holding nothing
UNKNOWN = "unknown"      # IMAP unreachable, so no claim about reality


@dataclass
class DraftState:
    """Gmail's answer for one claimed company."""

    company_slug: str
    contact_email: str
    state: str
    subject: str | None = None
    body: str | None = None
    gmail_url: str | None = None
    sent_date: str | None = None

    @property
    def needs_attention(self) -> bool:
        """True when the store disagrees with Gmail and a human should say
        which one is right."""
        return self.state in (SENT, MISSING)


def _credentials() -> tuple[str, str]:
    load_dotenv(ENV_PATH)
    user, password = os.getenv("GMAIL_USER"), os.getenv("GMAIL_APP_PASSWORD")
    if not user or not password:
        raise RuntimeError(f"GMAIL_USER / GMAIL_APP_PASSWORD not found in {ENV_PATH}")
    return user, password


def _plain_body(msg: Message) -> str:
    """The text/plain part, minus the signature the envelope adds.

    Stage 6 builds every draft as plain text plus an HTML alternative, so
    the plain part is the drafter's prose verbatim and is what an edit
    should round-trip through.
    """
    part = msg
    if msg.is_multipart():
        for candidate in msg.walk():
            if candidate.get_content_type() == "text/plain":
                part = candidate
                break
        else:
            return ""
    try:
        raw = part.get_payload(decode=True) or b""
    except Exception:
        return ""
    text = raw.decode(part.get_content_charset() or "utf-8", "replace")
    # SMTP transport rewrites every newline as CRLF on the way out, so the
    # body that comes back never matches what was appended until it is
    # normalised. Skipping this silently defeats the signature strip below.
    text = text.replace("\r\n", "\n")
    # _build_message appends this deterministically; the editable body is
    # everything above it.
    return text.rsplit("\n\nBest,\nYash", 1)[0].strip()


def _gmail_url(msgid_response: str) -> str | None:
    """Deep link to the draft in the Gmail web client.

    X-GM-MSGID comes back as a decimal 64-bit integer; the web client
    addresses messages by its hex form.
    """
    marker = "X-GM-MSGID "
    if marker not in msgid_response:
        return None
    tail = msgid_response.split(marker, 1)[1]
    digits = ""
    for char in tail:
        if char.isdigit():
            digits += char
        elif digits:
            break
    if not digits:
        return None
    return f"https://mail.google.com/mail/u/0/#drafts?compose={int(digits):x}"


def _search(imap: imaplib.IMAP4_SSL, *criteria: str) -> list[bytes]:
    try:
        typ, data = imap.uid("SEARCH", None, *criteria)
    except imaplib.IMAP4.error:
        return []
    if typ != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def inspect(claims: list[dict]) -> dict[str, DraftState]:
    """Ask Gmail about each claim. Keyed by company_slug.

    `claims` need only carry `company_slug`, `contact_email` and
    `draft_subject`. One connection serves every claim: Drafts and Sent are
    each selected once, and matching happens per claim against the messages
    found there.

    An unreachable mailbox yields UNKNOWN for everything rather than
    raising. Reporting "I could not check" is honest; reporting MISSING
    because the network blipped would invite a discard that throws away a
    real draft.
    """
    if not claims:
        return {}

    states = {
        c["company_slug"]: DraftState(
            company_slug=c["company_slug"],
            contact_email=c["contact_email"],
            state=UNKNOWN,
        )
        for c in claims
    }

    try:
        user, password = _credentials()
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
    except (RuntimeError, OSError):
        return states

    try:
        imap.login(user, password)

        # Each mailbox is selected exactly once, whatever the claim count.
        # Selecting per claim -- Drafts, Sent, back to Drafts -- made Gmail
        # drop the connection outright ("socket error: EOF") once a handful
        # of claims were in play.
        imap.select(DRAFTS_MAILBOX, readonly=True)
        unresolved = []
        for claim in claims:
            slug, to = claim["company_slug"], claim["contact_email"]
            for uid in _search(imap, "TO", to):
                typ, raw = imap.uid("FETCH", uid, "(X-GM-MSGID BODY.PEEK[])")
                if typ != "OK" or not raw or not raw[0]:
                    continue
                meta = raw[0][0].decode("utf-8", "replace")
                msg = email.message_from_bytes(raw[0][1])
                states[slug] = DraftState(
                    company_slug=slug,
                    contact_email=to,
                    state=IN_DRAFTS,
                    subject=msg["Subject"],
                    body=_plain_body(msg),
                    gmail_url=_gmail_url(meta),
                )
                break
            else:
                unresolved.append((slug, to))

        # Whatever is not in Drafts was either sent or deleted. Sent is the
        # consequential possibility, so it is ruled out before anything is
        # called missing.
        if unresolved:
            imap.select(SENT_MAILBOX, readonly=True)
            for slug, to in unresolved:
                sent_uids = _search(imap, "TO", to)
                if not sent_uids:
                    states[slug] = DraftState(
                        company_slug=slug, contact_email=to, state=MISSING
                    )
                    continue
                typ, raw = imap.uid("FETCH", sent_uids[-1], "(RFC822.HEADER)")
                header = (
                    email.message_from_bytes(raw[0][1])
                    if typ == "OK" and raw and raw[0]
                    else None
                )
                states[slug] = DraftState(
                    company_slug=slug,
                    contact_email=to,
                    state=SENT,
                    subject=header["Subject"] if header else None,
                    sent_date=(header["Date"] or "")[:31] if header else None,
                )
    except (imaplib.IMAP4.error, OSError):
        return states
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return states
