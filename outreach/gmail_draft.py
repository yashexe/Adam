"""
Stage 6/7 — put a draft into Gmail's Drafts folder.

Replaces both the review surface and the send step with something neither
had to be built: Gmail itself. The draft lands in the normal Drafts folder,
gets edited in the real mail client (or on a phone), and goes out when a
human presses Send. Nothing in this pipeline can send it.

That is a stronger form of the approval invariant than a chat gate, not a
weaker one. There is no code path here that transmits a message — the
IMAP connection only appends to a mailbox. Reaching a recipient requires a
person, in Gmail, choosing to.

Uses the existing Gmail app password (SMTP and IMAP both accept it). The
Gmail API would need OAuth and a Cloud project; IMAP APPEND needs neither.

    from outreach.gmail_draft import create_draft
    create_draft(to="someone@example.com", subject="...", body="...")
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import os
import time
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from dotenv import load_dotenv

from outreach.verify import VerificationResult

# Credentials live in job_search_automation; harvest/NOTES.md deliberately
# did not copy that .env. Reading it in place beats a second copy of a
# secret on disk.
ENV_PATH = Path("/Users/yashbhavsar/Code/job_search_automation/.env")
RESUME_PATH = (
    Path(__file__).resolve().parent.parent
    / "harvest/from_job_search_automation/Yash_Bhavsar_Resume_08192026.pdf"
)

IMAP_HOST = "imap.gmail.com"
DRAFTS_MAILBOX = '"[Gmail]/Drafts"'
SENT_MAILBOX = '"[Gmail]/Sent Mail"'

SENDER_NAME = "Yash Bhavsar"

# Lifted from job_search_automation's send_cold_email.py. The agent writes
# prose; the envelope — signature, attachment, markup — stays deterministic.
_SIGNATURE_HTML = """\
<p style="color: #666;">--<br>
<b>Yash Bhavsar</b><br>
Software Engineer<br>
<a href="https://www.linkedin.com/in/yash-bhav">LinkedIn</a> &middot;
<a href="https://github.com/yashexe">GitHub</a> &middot;
<a href="https://yashexe.github.io">yashexe.github.io</a><br>
Phone: +1 647-774-3765<br>
Email: <a href="mailto:yashbhavsar3602@gmail.com" style="color: #1155cc;">yashbhavsar3602@gmail.com</a>
</p>"""

_HTML_SHELL = """\
<html>
  <body style="font-family: Arial, sans-serif; font-size: 14px; color: #333;">
{paragraphs}
    <br>
    <p>Best,<br>Yash</p>
{signature}
  </body>
</html>"""


def _load_credentials() -> tuple[str, str]:
    load_dotenv(ENV_PATH)
    user, password = os.getenv("GMAIL_USER"), os.getenv("GMAIL_APP_PASSWORD")
    if not user or not password:
        raise RuntimeError(f"GMAIL_USER / GMAIL_APP_PASSWORD not found in {ENV_PATH}")
    return user, password


def _build_message(
    *, to: str, subject: str, body: str, sender: str, attach_resume: bool
) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = formataddr((SENDER_NAME, sender))
    msg["Subject"] = subject

    plain = f"{body.strip()}\n\nBest,\nYash"
    msg.set_content(plain)

    paragraphs = "\n".join(
        f"    <p>{para.strip()}</p>"
        for para in body.strip().split("\n\n")
        if para.strip()
    )
    msg.add_alternative(
        _HTML_SHELL.format(paragraphs=paragraphs, signature=_SIGNATURE_HTML),
        subtype="html",
    )

    if attach_resume:
        if not RESUME_PATH.exists():
            raise FileNotFoundError(f"resume not found at {RESUME_PATH}")
        msg.add_attachment(
            RESUME_PATH.read_bytes(),
            maintype="application",
            subtype="pdf",
            filename=RESUME_PATH.name,
        )
    return msg


def _append_draft(imap: imaplib.IMAP4_SSL, msg: EmailMessage) -> None:
    """The one way a message enters Drafts. Shared by all three creators so
    the mailbox, the flag, and the failure behavior cannot drift apart."""
    status, response = imap.append(
        DRAFTS_MAILBOX,
        "\\Draft",
        imaplib.Time2Internaldate(time.time()),
        msg.as_bytes(),
    )
    if status != "OK":
        raise RuntimeError(f"IMAP APPEND failed: {status} {response!r}")


def create_draft(
    *,
    to: str,
    subject: str,
    body: str,
    attach_resume: bool = True,
    verification: "VerificationResult | None" = None,
) -> None:
    """Append one draft to Gmail. Never sends.

    `verification` is stage 4's result. A blocking one refuses the append:
    under this design a draft sits one click from going out, so a
    known-undeliverable address must never become a draft in the first
    place. Passing None is allowed and means "stage 4 did not run" — that
    is a weaker state than a clean verification, not an equivalent one.
    """
    if verification is not None and verification.should_block:
        raise ValueError(
            f"refusing to draft to {to}: verification says {verification.label} "
            f"({verification.reason})"
        )
    user, password = _load_credentials()
    msg = _build_message(
        to=to, subject=subject, body=body, sender=user, attach_resume=attach_resume
    )

    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        imap.login(user, password)
        _append_draft(imap, msg)
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def create_reply_draft(*, to: str, body: str) -> str:
    """Append a reply draft into the thread of the most recent message sent
    to `to`. Returns the subject used. Never sends.

    Built for the one permitted follow-up bump (docs/decisions.md): the
    reply carries In-Reply-To/References from the original so Gmail threads
    it, and deliberately no résumé — it is already in the thread, and a
    bump that re-attaches it reads as a re-send rather than a nudge.

    Raises RuntimeError when the Sent folder holds nothing to this address:
    a bump to a message this account never sent would start a brand-new
    cold thread, which is exactly what the one-outreach dedup forbids.
    """
    user, password = _load_credentials()

    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        imap.login(user, password)
        imap.select(SENT_MAILBOX, readonly=True)
        typ, data = imap.uid("SEARCH", None, "TO", to)
        if typ != "OK" or not data or not data[0]:
            raise RuntimeError(
                f"no sent message to {to} found — cannot build a reply draft"
            )

        # IMAP SEARCH TO is a substring match over the whole header, so a
        # search for li@acme.com also returns mail to ali@acme.com. Walk
        # newest to oldest and take the first message actually addressed
        # to this exact mailbox — threading the bump onto someone else's
        # conversation is worse than not threading at all.
        original = None
        for uid in reversed(data[0].split()):
            typ, raw = imap.uid(
                "FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (TO SUBJECT MESSAGE-ID)])"
            )
            if typ != "OK" or not raw or not raw[0]:
                continue
            headers = email.message_from_bytes(raw[0][1])
            recipients = {
                addr.lower()
                for _, addr in email.utils.getaddresses(headers.get_all("To") or [])
            }
            if to.lower() in recipients:
                original = headers
                break
        if original is None:
            raise RuntimeError(
                f"no sent message addressed exactly to {to} found — cannot "
                f"build a reply draft"
            )

        # The raw header may arrive RFC2047-encoded; prefixing "Re: " onto
        # the encoded form would render as literal =?UTF-8?...?= garbage.
        raw_subject = (original["Subject"] or "").strip()
        original_subject = (
            str(make_header(decode_header(raw_subject))) if raw_subject else ""
        )
        message_id = (original["Message-ID"] or "").strip()

        subject = original_subject
        if subject and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        elif not subject:
            subject = "Re: my earlier email"

        msg = _build_message(
            to=to, subject=subject, body=body, sender=user, attach_resume=False
        )
        if message_id:
            msg["In-Reply-To"] = message_id
            msg["References"] = message_id

        _append_draft(imap, msg)
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return subject


def replace_draft(
    *,
    to: str,
    old_subject: str,
    subject: str,
    body: str,
    attach_resume: bool = True,
    verification: "VerificationResult | None" = None,
) -> int:
    """Edit a draft in place, as far as IMAP allows. Returns drafts replaced.

    IMAP has no edit: a draft is an immutable message in a mailbox, so an
    edit is an append plus a delete. The order is deliberate. Appending
    first means the worst case is a visible duplicate, which the next edit
    or a discard cleans up; deleting first means a failed append loses the
    only copy of prose a human may have spent time on.

    The old message is addressed by the UID captured before the append, not
    by re-matching on (to, subject) afterwards. An edit that leaves the
    subject unchanged would otherwise match the new draft too and delete
    what was just written.
    """
    if verification is not None and verification.should_block:
        raise ValueError(
            f"refusing to draft to {to}: verification says {verification.label} "
            f"({verification.reason})"
        )
    user, password = _load_credentials()
    msg = _build_message(
        to=to, subject=subject, body=body, sender=user, attach_resume=attach_resume
    )

    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    replaced = 0
    try:
        imap.login(user, password)
        imap.select(DRAFTS_MAILBOX)

        stale: list[bytes] = []
        typ, data = imap.uid("SEARCH", None, "TO", to)
        if typ == "OK" and data and data[0]:
            for uid in data[0].split():
                typ, raw = imap.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
                if typ != "OK" or not raw or not raw[0]:
                    continue
                header = raw[0][1].decode("utf-8", "replace")
                if old_subject.lower() in header.lower():
                    stale.append(uid)

        _append_draft(imap, msg)

        for uid in stale:
            typ, _ = imap.uid("COPY", uid, '"[Gmail]/Trash"')
            if typ == "OK":
                replaced += 1
        if stale:
            imap.expunge()
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return replaced


def trash_draft(*, to: str, subject: str) -> int:
    """Move matching drafts to Trash. Returns how many were moved.

    Uses UID commands throughout, deliberately. Sequence numbers renumber
    the moment a message leaves the mailbox, and Gmail's Trash is exclusive
    -- so COPY to Trash removes the message from Drafts immediately, and any
    follow-up STORE by sequence number lands on whatever slid into that
    slot. That mistake destroyed a good draft on 2026-08-23. UIDs are stable
    across expunges; sequence numbers are not.

    Moves to Trash rather than expunging: a draft expunged from Drafts
    carries no other label, so it is gone permanently with no undo.
    """
    user, password = _load_credentials()
    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    moved = 0
    try:
        imap.login(user, password)
        imap.select(DRAFTS_MAILBOX)
        typ, data = imap.uid("SEARCH", None, "SUBJECT", f'"{subject}"')
        if typ != "OK":
            return 0
        for uid in data[0].split():
            typ, raw = imap.uid("FETCH", uid, "(RFC822.HEADER)")
            if typ != "OK" or not raw or not raw[0]:
                continue
            header = raw[0][1].decode("utf-8", "replace")
            if to.lower() not in header.lower():
                continue
            typ, _ = imap.uid("COPY", uid, '"[Gmail]/Trash"')
            if typ == "OK":
                moved += 1
        imap.expunge()
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return moved
