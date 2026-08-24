"""
Prior-contact check against Gmail's sent mail.

The dedup store only knows what this pipeline did. It starts empty, and
Yash has been cold-emailing by hand for months. On 2026-08-23 the pipeline
drafted the target contact at company-a, three weeks after a hand-written email to
him had already gone out. The store could not have known.

Gmail's sent folder is the actual record of who has been contacted, so
that is what gets checked. Searching the recipient domain alone is not
enough: the company-a email went to `unrelated.domain@example.com`, a personal domain,
not `@company-a.com`. So the company name is searched across the whole
message too, which is broader and will sometimes match something
unrelated. That is the right side to err on. A false positive costs one
manual override; a false negative costs a duplicate cold email to someone
who already got one.
"""

from __future__ import annotations

import email
import imaplib
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path("/Users/yashbhavsar/Code/job_search_automation/.env")
IMAP_HOST = "imap.gmail.com"
SENT_MAILBOX = '"[Gmail]/Sent Mail"'


@dataclass
class PriorContact:
    date: str
    to: str
    subject: str

    def __str__(self) -> str:
        return f"{self.date} -> {self.to}: {self.subject}"


def _credentials() -> tuple[str, str]:
    load_dotenv(ENV_PATH)
    user, password = os.getenv("GMAIL_USER"), os.getenv("GMAIL_APP_PASSWORD")
    if not user or not password:
        raise RuntimeError(f"GMAIL_USER / GMAIL_APP_PASSWORD not found in {ENV_PATH}")
    return user, password


def prior_contacts(
    *, company_slug: str, domain: str | None = None, contact_name: str | None = None
) -> list[PriorContact]:
    """Anything already sent that looks like contact with this company.

    Returns [] when the mailbox cannot be reached — this is a safety check,
    not a gate that should take the pipeline down, and the store's own
    dedup still applies underneath it.
    """
    terms = {company_slug.lower()}
    if domain:
        terms.add(domain.lower())
        terms.add(domain.split(".")[0].lower())
    if contact_name:
        terms.add(contact_name.lower())

    # One search per term, unioned. imaplib cannot pass an OR expression as
    # a single SEARCH argument -- Gmail answers "Could not parse command" --
    # so the OR is done here instead.
    search_terms = [t for t in sorted(terms) if len(t) > 3]
    if not search_terms:
        return []

    try:
        user, password = _credentials()
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
    except (RuntimeError, OSError):
        return []

    found: list[PriorContact] = []
    try:
        imap.login(user, password)
        imap.select(SENT_MAILBOX, readonly=True)
        uids: set[bytes] = set()
        for term in search_terms:
            try:
                typ, data = imap.uid("SEARCH", None, "TEXT", term)
            except imaplib.IMAP4.error:
                continue
            if typ == "OK" and data and data[0]:
                uids.update(data[0].split())
        for uid in sorted(uids):
            typ, raw = imap.uid("FETCH", uid, "(RFC822.HEADER)")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            found.append(PriorContact(
                date=(msg["Date"] or "")[:31],
                to=msg["To"] or "",
                subject=msg["Subject"] or "",
            ))
    except (imaplib.IMAP4.error, OSError):
        return found
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return found
