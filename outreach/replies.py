"""
Did anyone write back?

`outreach_log` recorded that a message went out and nothing about what came
of it, which left the most important question about this pipeline —
does any of it work, and for which kind of contact — permanently
unanswerable. On 2026-08-24 an argument about whether recruiters are worth
emailing could not be settled either way, because two companies had ever
been logged and neither carried an outcome.

So this reads Gmail and reports, per contacted company, whether a human
replied. Strictly read-only.

**Threads, not addresses.** Searching for mail *from* the contact misses
the most interesting outcome: a recruiter who forwards to the hiring
manager, who then replies. Gmail exposes a stable conversation id
(`X-GM-THRID`) on the sent message, so the whole thread is fetched and any
message not written by Yash counts.

Bounces are separated out rather than counted as replies. A bounce is not
silence and it is not interest: it means the address was wrong, which is
exactly the failure `outreach/verify.py` cannot currently catch, since it
confirms a mailbox is deliverable without confirming whose it is.

    from outreach.replies import check
    results = check([{"company_slug": "company-b",
                      "contact_email": "contact@company-b.co"}])
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path("/Users/yashbhavsar/Code/job_search_automation/.env")
IMAP_HOST = "imap.gmail.com"
SENT_MAILBOX = '"[Gmail]/Sent Mail"'
ALL_MAILBOX = '"[Gmail]/All Mail"'

REPLIED = "replied"
BOUNCED = "bounced"
SILENT = "silent"
UNKNOWN = "unknown"  # could not reach Gmail; not the same as silence

# Automated senders that answer a cold email without a human being involved.
_DAEMON = re.compile(
    r"mailer-daemon|postmaster|no-?reply|donotreply|notification|"
    r"bounces?@|delivery(status|subsystem)",
    re.IGNORECASE,
)


@dataclass
class ReplyState:
    company_slug: str
    contact_email: str
    state: str
    replied_at: str | None = None
    reply_from: str | None = None
    subject: str | None = None

    @property
    def is_reply(self) -> bool:
        return self.state == REPLIED


def _credentials() -> tuple[str, str]:
    load_dotenv(ENV_PATH)
    user, password = os.getenv("GMAIL_USER"), os.getenv("GMAIL_APP_PASSWORD")
    if not user or not password:
        raise RuntimeError(f"GMAIL_USER / GMAIL_APP_PASSWORD not found in {ENV_PATH}")
    return user, password


def _search(imap: imaplib.IMAP4_SSL, *criteria: str) -> list[bytes]:
    try:
        typ, data = imap.uid("SEARCH", None, *criteria)
    except imaplib.IMAP4.error:
        return []
    if typ != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def _thread_id(meta: str) -> str | None:
    marker = "X-GM-THRID "
    if marker not in meta:
        return None
    digits = ""
    for char in meta.split(marker, 1)[1]:
        if char.isdigit():
            digits += char
        elif digits:
            break
    return digits or None


def _addr(header: str | None) -> str:
    return email.utils.parseaddr(header or "")[1].lower()


def _as_iso(date_header: str | None) -> str | None:
    """RFC-2822 date header to a UTC timestamp matching the store's format."""
    if not date_header:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def check(contacts: list[dict]) -> dict[str, ReplyState]:
    """Look for a reply to each contacted company. Keyed by company_slug.

    `contacts` need only carry `company_slug` and `contact_email`. Both
    mailboxes are selected once for the whole batch: switching per contact
    is what made an earlier version of this pattern get its connection
    dropped by Gmail.

    An unreachable mailbox yields UNKNOWN for everything. Reporting "could
    not check" is honest; reporting SILENT because the network blipped
    would quietly become evidence in an argument it should not settle.
    """
    if not contacts:
        return {}

    out = {
        c["company_slug"]: ReplyState(
            company_slug=c["company_slug"],
            contact_email=c["contact_email"],
            state=UNKNOWN,
        )
        for c in contacts
    }

    try:
        user, password = _credentials()
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
    except (RuntimeError, OSError):
        return out

    try:
        imap.login(user, password)
        me = user.lower()

        # Pass one: the thread id of what was sent to each contact.
        imap.select(SENT_MAILBOX, readonly=True)
        threads: dict[str, str] = {}
        for contact in contacts:
            slug, to = contact["company_slug"], contact["contact_email"]
            uids = _search(imap, "TO", to)
            if not uids:
                # Nothing sent from this account -- e.g. mailed by hand from
                # somewhere else. Silence here is not evidence of anything.
                continue
            typ, raw = imap.uid("FETCH", uids[-1], "(X-GM-THRID)")
            if typ != "OK" or not raw or not raw[0]:
                continue
            meta = raw[0].decode("utf-8", "replace") if isinstance(raw[0], bytes) \
                else raw[0][0].decode("utf-8", "replace")
            thrid = _thread_id(meta)
            if thrid:
                threads[slug] = thrid
            out[slug].state = SILENT

        # Pass two: everything else in those threads.
        if threads:
            imap.select(ALL_MAILBOX, readonly=True)
            for slug, thrid in threads.items():
                for uid in _search(imap, "X-GM-THRID", thrid):
                    typ, raw = imap.uid("FETCH", uid, "(RFC822.HEADER)")
                    if typ != "OK" or not raw or not raw[0]:
                        continue
                    msg = email.message_from_bytes(raw[0][1])
                    sender = _addr(msg["From"])
                    if not sender or sender == me:
                        continue  # his own message in his own thread
                    state = BOUNCED if _DAEMON.search(sender) else REPLIED
                    out[slug] = ReplyState(
                        company_slug=slug,
                        contact_email=out[slug].contact_email,
                        state=state,
                        replied_at=_as_iso(msg["Date"]),
                        reply_from=sender,
                        subject=msg["Subject"],
                    )
                    if state == REPLIED:
                        break  # a human beats a daemon; stop looking
    except (imaplib.IMAP4.error, OSError):
        return out
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return out


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
