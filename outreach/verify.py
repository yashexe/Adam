"""
Stage 4 — deterministic email verification.

Sits between Agent 1 and Agent 2 on purpose. Agent 1 produces candidate
people; this step resolves and checks their addresses and reduces
everything it learns to a single label. Agent 2 receives the label and the
address and nothing else — not the SMTP details, not any provider's raw
JSON, not Agent 1's source notes. Restricting what reaches the model is
the point, not just gating its output.

Two distinct jobs live here, split across vendors on purpose since
2026-08-29:

- **The domain roster** (pattern + per-address names) comes from Hunter's
  domain-search and nothing else — it is the input to `resolve_address`
  and the `_name_conflict` identity guard, and no free alternative with
  comparable small-company coverage exists. Hunter's free credits are
  reserved for this: ~1 cached credit per company.
- **Mailbox probing** is a commodity and walks `_VERIFY_PROVIDERS` — a
  direct SMTP probe from this Mac first (keyless and free; benchmarked
  2026-09-02 against Hunter's own labels it settles about half of all
  addresses outright and calls most of the rest catch-all, a provisional
  verdict Hunter then gets a turn to sharpen), then free-tier vendors
  (ZeroBounce, MillionVerifier; each activates when its key is in
  `.env`), Hunter last. One 12-company drain run (2026-08-28) consumed a
  whole Hunter month mostly on probes; this split roughly halves that.

Nothing here raises into the pipeline. An unreachable API, a missing key,
or an exhausted quota all degrade to `UNVERIFIED`, which is honest and lets
a human decide. A verification step that can halt the pipeline is worse
than one that says "I don't know".

`verify_email` caches by address so re-running the pipeline over the same
company is free, whichever provider answered.

    from outreach.verify import verify_email
    result = verify_email("constructed.guess@company-a.com")
    result.label, result.should_block
"""

from __future__ import annotations

import json
import os
import re
import secrets
import smtplib
import socket
import ssl
import subprocess
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
CACHE_PATH = Path(__file__).resolve().parent.parent / ".cache" / "hunter.json"

VERIFIER_URL = "https://api.hunter.io/v2/email-verifier"
DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"
ACCOUNT_URL = "https://api.hunter.io/v2/account"
_TIMEOUT = 20

# Labels, ordered worst to best. Only INVALID blocks: everything else is a
# judgement a human is better placed to make than a threshold is.
INVALID = "invalid"
UNVERIFIED = "unverified"
CATCH_ALL = "catch_all"
RISKY = "risky"
VERIFIED = "verified"


@dataclass
class VerificationResult:
    email: str
    label: str
    score: int | None = None
    reason: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def should_block(self) -> bool:
        """Only a confirmed-undeliverable address stops the pipeline.

        Under the Gmail-drafts design a draft sits one click from sending,
        so a known-bad address must not become one. Everything short of
        that reaches a human with its label attached.
        """
        return self.label == INVALID

    def __str__(self) -> str:
        score = f" score={self.score}" if self.score is not None else ""
        return f"{self.email}: {self.label}{score} ({self.reason})"


def _api_key() -> str | None:
    load_dotenv(ENV_PATH)
    return os.getenv("HUNTER_API_KEY") or None


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2))


def _get(url: str, params: dict) -> tuple[dict | None, str]:
    """(payload, error). Never raises — the caller degrades to UNVERIFIED."""
    query = urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(f"{url}?{query}", timeout=_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8")), ""
    except urllib.error.HTTPError as exc:
        # Hunter puts a usable message in the body on 4xx (bad key, quota).
        try:
            detail = json.loads(exc.read().decode("utf-8"))
            errors = detail.get("errors") or [{}]
            return None, f"HTTP {exc.code}: {errors[0].get('details', exc.reason)}"
        except Exception:
            return None, f"HTTP {exc.code}: {exc.reason}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _label_from(data: dict) -> tuple[str, str]:
    """Reduce Hunter's verifier payload to one label and a short reason."""
    result = (data.get("result") or "").lower()
    status = (data.get("status") or "").lower()

    if result == "undeliverable" or status == "invalid":
        return INVALID, f"Hunter reports {status or result}"
    if data.get("disposable"):
        return INVALID, "disposable address domain"
    # accept_all is the common case at small companies: the domain accepts
    # mail for any local part, so no service can confirm this specific
    # mailbox. Distinguished from generic risk because the cause is known
    # and it says nothing bad about the address.
    if data.get("accept_all") or status == "accept_all":
        return CATCH_ALL, "domain accepts all mail; mailbox unconfirmable"
    if result == "risky" or status in ("webmail", "unknown"):
        return RISKY, f"Hunter reports {status or result}"
    if result == "deliverable" or status == "valid":
        return VERIFIED, "SMTP-confirmed deliverable"
    return UNVERIFIED, f"unrecognized response ({status or result or 'empty'})"


# ── Verification providers ─────────────────────────────────────────────────
# Mailbox probing is a commodity; the domain roster is not. Hunter's free
# credits are therefore reserved for what only Hunter provides here
# (domain-search: pattern + per-address names, see confirm_pattern), and
# plain verification walks this chain instead: the direct SMTP probe, then
# free-tier vendors, Hunter last. Each adapter returns a VerificationResult
# on a conclusive answer, or a string saying why it must be skipped (no
# key, quota gone, API error, could not conclude) so the next provider
# gets a turn. Every provider normalizes onto the same five labels;
# `should_block` semantics are identical regardless of who answered.
#
# Inconclusive is a skip, not a label. A vendor's "unknown" usually means
# its probe was greylisted or timed out from *its* network — exactly the
# case a second opinion from a different vantage point resolves, and
# exactly the answer that must never be cached as if it were a fact. Only
# conclusive answers reach the cache. (Until 2026-09-02 an "unknown" was
# cached as RISKY forever.)
#
# The chain dates from 2026-08-29, after one 12-company drain run consumed
# the entire Hunter month. The SMTP probe went in front on 2026-09-02, when
# it turned out port 25 is open from this Mac to Google's MX and 26 of the
# 29 domains this pipeline had ever verified are hosted there. Benchmarked
# the same day against all 40 addresses Hunter had labeled: 21 identical
# verdicts, 16 where the probe could only say catch-all (12 domains that
# accept any local part from here — Hunter had a definite answer for 15),
# 3 skips (two Microsoft-hosted domains that refuse residential
# connections, one Proofpoint), and one outright contradiction where the
# mailbox Hunter had called dead now accepts mail.

_SMTP_TIMEOUT = 8
_UNREACHABLE_TTL_SECONDS = 24 * 3600  # an MX that drops us is retried daily
_ENHANCED_CODE = re.compile(r"\b([245])\.(\d{1,3})\.(\d{1,3})\b")

# RFC 3463 enhanced codes that are the server's verdict on the *mailbox*.
# Everything else a server can say is about this sender, this IP, or this
# moment, and is a skip rather than a label.
_NO_SUCH_MAILBOX = frozenset({
    "5.1.0", "5.1.1", "5.1.2", "5.1.3", "5.1.6", "5.1.10",
    "5.4.1",  # Microsoft's directory-based edge block for unknown recipients
    "5.2.1",  # mailbox exists but is disabled — mail to it bounces all the same
})
_MAILBOX_FULL = frozenset({"5.2.2"})


def _mx_hosts(domain: str) -> tuple[list[str], str]:
    """Lowest-preference-first MX hosts, or ([], why-not).

    Uses `dig` (on every Mac) because the stdlib has no MX lookup. A
    failed lookup is a skip, never a verdict: a DNS hiccup must not become
    INVALID and block a draft.
    """
    try:
        proc = subprocess.run(
            ["dig", "+short", "+time=3", "+tries=1", "MX", domain],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], f"MX lookup failed ({type(exc).__name__})"
    if proc.returncode != 0:
        return [], f"MX lookup failed (dig exit {proc.returncode})"
    pairs = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            pairs.append((int(parts[0]), parts[1].rstrip(".").lower()))
    if not pairs:
        return [], "no MX record"
    return [host for _, host in sorted(pairs)], ""


def _smtp_reply(message: bytes) -> tuple[str, str]:
    """(enhanced status code or '', one-line reply text)."""
    text = " ".join(message.decode("utf-8", "replace").split())
    match = _ENHANCED_CODE.search(text)
    return (".".join(match.groups()) if match else ""), text


def _quietly_quit(server) -> None:
    try:
        server.quit()
    except Exception:
        pass


def _open_probe_session(domain: str):
    """An SMTP session ready for RCPT TO at the domain's lowest-preference
    MX, or a skip string saying why there cannot be one. EHLO, STARTTLS
    when offered, MAIL FROM the null sender. Callers must `_quietly_quit`."""
    cache = _load_cache()
    stale = cache.get(f"unreachable:{domain}")
    if stale and time.time() - stale.get("cached_at", 0) < _UNREACHABLE_TTL_SECONDS:
        return f"smtp: {stale.get('why', 'unreachable')} (remembered; retried daily)"
    hosts, why = _mx_hosts(domain)
    if not hosts:
        return f"smtp: {why} for {domain}"
    host = hosts[0]
    try:
        server = smtplib.SMTP(
            host, 25, timeout=_SMTP_TIMEOUT,
            local_hostname=socket.gethostname() or "adam.local",
        )
    except (OSError, smtplib.SMTPException) as exc:
        # A slate of three candidates at a Proofpoint-hosted domain cost
        # 16 s each on the 2026-09-02 benchmark; remember the dead door.
        why = f"cannot reach {host}:25 ({type(exc).__name__}: {exc})"
        cache[f"unreachable:{domain}"] = {"why": why, "cached_at": time.time()}
        _save_cache(cache)
        return f"smtp: {why}"
    try:
        if server.ehlo()[0] != 250:
            server.helo()
        if server.has_extn("starttls"):
            # Being a well-behaved client, not securing a payload: there is
            # no message. MX certificates are routinely issued for a name
            # other than the MX host, so verification would only add a
            # failure mode to a probe that carries nothing.
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            server.starttls(context=context)
            server.ehlo()
        code, msg = server.mail("")
        if code != 250:
            _quietly_quit(server)
            return f"smtp: {host} refused the null sender ({code} {_smtp_reply(msg)[1][:80]})"
    except (OSError, smtplib.SMTPException) as exc:
        _quietly_quit(server)
        return f"smtp: session with {host} failed ({type(exc).__name__}: {exc})"
    return host, server


def _rcpt_verdict(server, address: str) -> tuple[str | None, dict]:
    """The server's verdict on one mailbox: VERIFIED / INVALID / RISKY, or
    None when the reply is about this sender, this IP, or this moment
    rather than the mailbox. `raw` carries the reply either way."""
    code, msg = server.rcpt(address)
    enhanced, text = _smtp_reply(msg)
    raw = {"code": code, "enhanced": enhanced, "message": text[:200]}
    if code in (250, 251):
        return VERIFIED, raw
    if enhanced in _NO_SUCH_MAILBOX:
        return INVALID, raw
    if enhanced in _MAILBOX_FULL:
        return RISKY, raw
    return None, raw


def _accepts_random(server, domain: str, raw: dict) -> bool:
    """Does the server say yes to a local part nobody could have? Then it
    says yes to everything, and no probe can tell one mailbox from
    another there."""
    probe = f"{secrets.token_hex(6)}.zq@{domain}"
    pcode, _ = server.rcpt(probe)
    raw["catch_all_probe"] = pcode
    return pcode in (250, 251)


def _catch_all_cached(cache: dict, domain: str) -> bool | None:
    entry = cache.get(f"catchall:{domain}")
    if entry and time.time() - entry.get("cached_at", 0) < _DOMAIN_TTL_SECONDS:
        return bool(entry.get("catch_all"))
    return None


def _remember_catch_all(cache: dict, domain: str, flag: bool) -> None:
    cache[f"catchall:{domain}"] = {"catch_all": flag, "cached_at": time.time()}
    _save_cache(cache)


def _verify_via_smtp(email: str) -> VerificationResult | str:
    """Ask the domain's own mail server whether the mailbox exists.

    The same RCPT TO handshake every verification vendor sells, done from
    here: connect to the lowest-preference MX on port 25, EHLO, STARTTLS
    when offered, MAIL FROM the null sender, RCPT TO the address, then
    RCPT TO a random local part so a domain that says yes to everything is
    labeled CATCH_ALL rather than VERIFIED. No message is ever sent — the
    session ends before DATA. A handful of probes a day from a home
    connection is far below anything Google rate-limits, and the actual
    emails go out through Gmail's servers, so this IP's reputation is
    never what a recipient sees.

    Only the server's verdict on the mailbox becomes a label. A refusal
    aimed at this sender or IP (5.7.x policy), a temporary code
    (greylisting, any 4xx), an unreachable MX, or a missing MX all return
    a skip string, so a vendor with a better vantage point gets the next
    turn.

    Measured 2026-09-02 from this Mac: the ISP blocks outbound port 25
    over IPv4, so every probe rides IPv6 — Google's MX answers in under a
    second there, and if IPv6 ever goes away every probe becomes a skip
    after the connect timeout and the chain falls through to the vendors.
    Microsoft 365 is intermittent over IPv6: the same MX alternates
    between a real verdict and a 5.7.1 refusal of the client host, and
    both are handled — verdict when given, skip otherwise.
    """
    domain = email.rsplit("@", 1)[-1].lower()
    cache = _load_cache()
    if _catch_all_cached(cache, domain):
        return VerificationResult(
            email=email, label=CATCH_ALL,
            reason="domain accepts all mail; mailbox unconfirmable (domain fact cached)",
            raw={"catch_all_cached": True},
        )
    session = _open_probe_session(domain)
    if isinstance(session, str):
        return session
    host, server = session
    raw: dict = {"mx": host}
    try:
        label, reply = _rcpt_verdict(server, email)
        raw.update(reply)
        if label is None:
            kind = ("temporary refusal" if 400 <= reply["code"] < 500
                    else "refused without a mailbox verdict")
            return f"smtp: {host} {kind} ({reply['code']} {reply['enhanced']} {reply['message'][:80]})"
        if label == VERIFIED:
            if _accepts_random(server, domain, raw):
                _remember_catch_all(cache, domain, True)
                return VerificationResult(
                    email=email, label=CATCH_ALL,
                    reason="domain accepts all mail; mailbox unconfirmable", raw=raw,
                )
            _remember_catch_all(cache, domain, False)
            return VerificationResult(
                email=email, label=VERIFIED,
                reason=f"SMTP-confirmed deliverable by {host}", raw=raw,
            )
        if label == INVALID:
            return VerificationResult(
                email=email, label=INVALID,
                reason=f"{host} reports no such mailbox ({reply['code']} {reply['enhanced']})", raw=raw,
            )
        return VerificationResult(
            email=email, label=RISKY,
            reason=f"{host} reports the mailbox is full ({reply['code']} {reply['enhanced']})", raw=raw,
        )
    except (OSError, smtplib.SMTPException) as exc:
        return f"smtp: session with {host} failed ({type(exc).__name__}: {exc})"
    finally:
        _quietly_quit(server)


def _verify_via_zerobounce(email: str) -> VerificationResult | str:
    """https://api.zerobounce.net/v2/validate — free tier ~100/month.

    Checked against the v2 docs 2026-09-02: params `api_key` and `email`;
    statuses valid / invalid / catch-all / unknown / spamtrap / abuse /
    do_not_mail, with detail in `sub_status`; failures put text in an
    `error` field ("Invalid API Key or your account ran out of credits").
    Zero-cost smoke test once a key exists: the sandbox local parts
    valid, invalid, catch-all and unknown at the example.com domain return
    canned results without spending a credit.
    """
    load_dotenv(ENV_PATH)
    key = os.getenv("ZEROBOUNCE_API_KEY")
    if not key:
        return "zerobounce: no ZEROBOUNCE_API_KEY"
    payload, error = _get("https://api.zerobounce.net/v2/validate",
                          {"api_key": key, "email": email})
    if payload is None:
        return f"zerobounce: {error}"
    if payload.get("error"):
        return f"zerobounce: {payload['error']}"
    status = (payload.get("status") or "").lower()
    sub = (payload.get("sub_status") or "").lower()
    if status == "unknown":
        return f"zerobounce: could not conclude ({sub or 'no detail'})"
    known = {
        "valid": (VERIFIED, "SMTP-confirmed deliverable"),
        "invalid": (INVALID, f"ZeroBounce reports invalid ({sub or 'no detail'})"),
        "catch-all": (CATCH_ALL, "domain accepts all mail; mailbox unconfirmable"),
        # A spamtrap is a real, deliverable mailbox that exists to burn
        # sender reputation — blocking is the only sane response.
        "spamtrap": (INVALID, "ZeroBounce flags a spamtrap"),
        "abuse": (RISKY, "ZeroBounce flags an abuse-prone address"),
        "do_not_mail": (
            INVALID if sub in ("disposable", "toxic") else RISKY,
            f"ZeroBounce reports do_not_mail ({sub or 'no detail'})",
        ),
    }
    if status not in known:
        return f"zerobounce: unrecognized status ({status or 'empty'})"
    label, reason = known[status]
    return VerificationResult(email=email, label=label, reason=reason, raw=payload)


def _verify_via_millionverifier(email: str) -> VerificationResult | str:
    """https://api.millionverifier.com/api/v3 — pay-as-you-go with free credits.

    Checked live against the documented demo keys 2026-09-02 (the API
    answers API_KEY_FOR_OK, API_KEY_FOR_INVALID, ... with canned
    responses and no account): params `api`, `email`, `timeout`; results
    ok / invalid / disposable / catch_all / unknown / unverified / error,
    detail in `subresult`; failures put text in `error` ("Apikey not
    found", "Insufficient credits", "IP address blocked"). Those demo keys
    are also the zero-cost smoke test for this adapter.
    """
    load_dotenv(ENV_PATH)
    key = os.getenv("MILLIONVERIFIER_API_KEY")
    if not key:
        return "millionverifier: no MILLIONVERIFIER_API_KEY"
    payload, error = _get("https://api.millionverifier.com/api/v3/",
                          {"api": key, "email": email, "timeout": 15})
    if payload is None:
        return f"millionverifier: {error}"
    if payload.get("error"):
        return f"millionverifier: {payload['error']}"
    result = (payload.get("result") or "").lower()
    sub = (payload.get("subresult") or "").lower()
    if result in ("unknown", "unverified", "error", ""):
        return f"millionverifier: could not conclude ({result or 'empty'}/{sub or 'no detail'})"
    known = {
        "ok": (VERIFIED, "SMTP-confirmed deliverable"),
        "invalid": (INVALID, f"MillionVerifier reports invalid ({sub or 'no detail'})"),
        "disposable": (INVALID, "disposable address domain"),
        "catch_all": (CATCH_ALL, "domain accepts all mail; mailbox unconfirmable"),
    }
    if result not in known:
        return f"millionverifier: unrecognized result ({result})"
    label, reason = known[result]
    return VerificationResult(email=email, label=label, reason=reason, raw=payload)


def _verify_via_hunter(email: str) -> VerificationResult | str:
    key = _api_key()
    if not key:
        return f"hunter: no HUNTER_API_KEY in {ENV_PATH}"
    payload, error = _get(VERIFIER_URL, {"email": email, "api_key": key})
    if payload is None:
        return f"hunter: {error}"
    data = payload.get("data") or {}
    if (data.get("status") or "").lower() == "unknown":
        return "hunter: could not conclude"
    label, reason = _label_from(data)
    return VerificationResult(email=email, label=label,
                              score=data.get("score"), reason=reason, raw=data)


# (name, adapter, can it improve on a CATCH_ALL verdict from earlier in the
# chain). A live probe — ours or a vendor's — cannot tell one mailbox from
# another on a domain that accepts everything, so a catch-all verdict is
# held as *provisional* and the chain continues only through providers
# that bring more than a probe. Hunter does: its verdict draws on its
# address sources and bounce history, and on 2026-09-02's benchmark it
# returned a definite valid/invalid for 15 of the 16 addresses this
# probe could only call catch-all. Providers that cannot improve on it
# are skipped rather than spending a credit to repeat it. If nobody can
# sharpen it, the provisional catch-all stands, cached — it is a real
# fact about the domain, not an inconclusive answer.
_VERIFY_PROVIDERS = (
    ("smtp", _verify_via_smtp, False),
    ("zerobounce", _verify_via_zerobounce, False),
    ("millionverifier", _verify_via_millionverifier, False),
    ("hunter", _verify_via_hunter, True),
)


def _settle(cache: dict, email: str, name: str, outcome: VerificationResult,
            skips: list[str]) -> VerificationResult:
    """Cache a conclusive answer and stamp it with who gave it."""
    reason = outcome.reason if name == "hunter" else f"{outcome.reason} (via {name})"
    if skips:
        reason += "; " + "; ".join(skips)
    cache[email] = {
        "label": outcome.label,
        "score": outcome.score,
        "reason": reason,
        "provider": name,
        "raw": outcome.raw,
    }
    _save_cache(cache)
    outcome.reason = reason
    return outcome


def verify_email(email: str, *, use_cache: bool = True) -> VerificationResult:
    """Check one address via the first provider able to answer.

    The SMTP probe answers for free wherever it can; a credit is spent only
    at whichever vendor answers after it, and only conclusive answers are
    cached. The terminal UNVERIFIED carries every provider's reason for
    passing, so "why is this unverified" is always answerable.
    """
    cache = _load_cache()
    if use_cache and email in cache:
        entry = cache[email]
        return VerificationResult(
            email=email,
            label=entry["label"],
            score=entry.get("score"),
            reason=entry.get("reason", "") + " (cached)",
            raw=entry.get("raw", {}),
        )

    skips: list[str] = []
    provisional: tuple[str, VerificationResult] | None = None
    for index, (name, provider, sharpens_catch_all) in enumerate(_VERIFY_PROVIDERS):
        if provisional and not sharpens_catch_all:
            continue  # a second probe of a catch-all domain buys nothing
        outcome = provider(email)
        if isinstance(outcome, str):
            skips.append(outcome)
            continue
        later_can_sharpen = any(s for _, _, s in _VERIFY_PROVIDERS[index + 1:])
        if outcome.label == CATCH_ALL and provisional is None and later_can_sharpen:
            provisional = (name, outcome)
            continue
        return _settle(cache, email, name, outcome, [])

    if provisional:
        name, outcome = provisional
        return _settle(cache, email, name, outcome, skips)
    return VerificationResult(
        email=email, label=UNVERIFIED,
        reason="no provider could answer: " + "; ".join(skips),
    )


# A week: long enough that a slate resolution and its finalize share one
# search credit, short enough that Hunter indexing a small company's domain
# — exactly the coverage gap this pipeline lives in — shows up on a later
# attempt instead of being masked by a stale cache forever.
_DOMAIN_TTL_SECONDS = 7 * 24 * 3600


def confirm_pattern(
    domain: str, *, use_cache: bool = True
) -> tuple[str | None, str, list[dict]]:
    """Hunter's own view of a domain's address pattern, for corroborating
    what Agent 1 inferred, plus the per-address names behind it. The name
    list is what catches a pattern-derived address that happens to already
    belong to someone else — see `_name_conflict`.

    Cached per domain for a week, because with slates (several candidate
    contacts at one company) the same roster would otherwise be bought once
    per candidate. Two deliberate exclusions: failed calls (a transient
    fact, not a finding) and empty results — "Hunter knows nothing about
    this domain yet" is the answer most likely to have changed by the next
    attempt, and caching it would make a small company permanently
    unresolvable. Cached entries keep only the three fields the name
    checks read; Hunter's per-address metadata (sources, scores) would
    bloat a cache file that is rewritten whole on every save. Cache keys
    are `domain:{domain}`, which cannot collide with the verifier's
    per-address keys (those contain @).
    """
    cache = _load_cache()
    cache_key = f"domain:{domain}"
    if use_cache and cache_key in cache:
        entry = cache[cache_key]
        if time.time() - entry.get("cached_at", 0) < _DOMAIN_TTL_SECONDS:
            return entry["pattern"], entry["note"] + " (cached)", entry["emails"]

    key = _api_key()
    if not key:
        return None, f"no HUNTER_API_KEY in {ENV_PATH}", []
    payload, error = _get(DOMAIN_SEARCH_URL, {"domain": domain, "api_key": key})
    if payload is None:
        return None, error, []
    data = payload.get("data") or {}
    emails = [
        {
            "value": entry.get("value"),
            "first_name": entry.get("first_name"),
            "last_name": entry.get("last_name"),
        }
        for entry in (data.get("emails") or [])
    ]
    pattern, note = data.get("pattern"), f"{len(emails)} addresses seen"
    if pattern or emails:
        cache[cache_key] = {
            "pattern": pattern, "note": note, "emails": emails,
            "cached_at": time.time(),
        }
        _save_cache(cache)
    return pattern, note, emails


def provider_status() -> list[dict]:
    """One row per provider in chain order: can it answer right now, and
    what does it have left. Backs `outreach_run.py verifiers`, the thing to
    run before a drain and right after adding a key."""
    rows: list[dict] = []
    try:
        with socket.create_connection(("aspmx.l.google.com", 25), timeout=5) as sock:
            family = "IPv6" if sock.family == socket.AF_INET6 else "IPv4"
        rows.append({"provider": "smtp", "ready": True,
                     "detail": f"port 25 open to Google's MX over {family}; "
                               "free, no key, no quota"})
    except OSError as exc:
        rows.append({"provider": "smtp", "ready": False,
                     "detail": f"port 25 blocked from this network ({type(exc).__name__})"})

    load_dotenv(ENV_PATH)
    key = os.getenv("ZEROBOUNCE_API_KEY")
    if not key:
        rows.append({"provider": "zerobounce", "ready": False,
                     "detail": "no ZEROBOUNCE_API_KEY in .env (free tier ~100/month)"})
    else:
        payload, error = _get("https://api.zerobounce.net/v2/getcredits", {"api_key": key})
        credits = str((payload or {}).get("Credits", "")) if payload else ""
        if payload is None:
            rows.append({"provider": "zerobounce", "ready": False, "detail": error})
        elif credits in ("", "-1"):
            rows.append({"provider": "zerobounce", "ready": False, "detail": "key rejected"})
        else:
            rows.append({"provider": "zerobounce", "ready": credits != "0",
                         "detail": f"{credits} credits left"})

    key = os.getenv("MILLIONVERIFIER_API_KEY")
    if not key:
        rows.append({"provider": "millionverifier", "ready": False,
                     "detail": "no MILLIONVERIFIER_API_KEY in .env (free signup credits)"})
    else:
        payload, error = _get("https://api.millionverifier.com/api/v3/credits", {"api": key})
        if payload is None:
            rows.append({"provider": "millionverifier", "ready": False, "detail": error})
        elif payload.get("error"):
            rows.append({"provider": "millionverifier", "ready": False,
                         "detail": str(payload["error"])})
        else:
            credits = payload.get("credits")
            rows.append({"provider": "millionverifier", "ready": bool(credits),
                         "detail": f"{credits} credits left"})

    available, note = credits_remaining()
    rows.append({"provider": "hunter", "ready": bool(available),
                 "detail": note + " (reserved for the domain roster)"})

    key = os.getenv("APOLLO_API_KEY")
    if not key:
        rows.append({"provider": "apollo", "ready": False,
                     "detail": "no APOLLO_API_KEY in .env (free person-enrichment tier; "
                               "resolution only, not a mailbox verifier)"})
    else:
        rows.append({"provider": "apollo", "ready": True,
                     "detail": "key present; Apollo has no credits-remaining endpoint, "
                               "so readiness here means only that a key exists"})
    return rows


def credits_remaining() -> tuple[int | None, str]:
    """Free tier is 50/month; worth checking before a batch run."""
    key = _api_key()
    if not key:
        return None, f"no HUNTER_API_KEY in {ENV_PATH}"
    payload, error = _get(ACCOUNT_URL, {"api_key": key})
    if payload is None:
        return None, error
    data = payload.get("data") or {}
    requests = data.get("requests") or {}
    searches = requests.get("searches") or {}
    verifications = requests.get("verifications") or {}
    available = verifications.get("available", searches.get("available"))
    used = verifications.get("used", searches.get("used"))
    if available is None:
        return None, "unknown"
    note = f"used {used} of {available}"
    if data.get("reset_date"):
        note += f", resets {data['reset_date']}"
    return max(available - (used or 0), 0), note


# ── Address resolution ─────────────────────────────────────────────────────
# Pattern inference belongs here, not in Agent 1.
#
# The company-a case (2026-08-23) showed why. Agent 1 followed its
# instructions exactly: find a real address at the domain, infer the
# pattern from it. It found press.contact@company-a.com on the company's
# own press page — genuine evidence, correctly sourced, and it verifies at
# score 100. But company-a's actual convention is {first}: that one press
# contact is the exception, not the rule, and generalizing from it produced
# constructed.guess@company-a.com, which does not exist.
#
# One real address is a sample of size one. Hunter sees the whole domain
# for the same credit, so the deterministic layer should own this.
#
# Deliverable is not the same as belonging to the right person. The
# company-c case (2026-08-24) showed why: applying `{first}{l}` to "[the contact]" renders wrong.mailbox@company-c.io, a real mailbox that verifies at score
# 100 -- and belongs to a different, unrelated employee. Hunter's domain-search
# response names the person behind each address it has seen; confirm_pattern
# now returns that list instead of discarding it, and _name_conflict checks
# a candidate against it before resolve_address will use it. Caught by hand
# that time; now it cannot happen silently.

# Role accounts are never outreach targets -- the whole point of this
# pipeline is reaching a person rather than a shared inbox, and
# contact-finder is explicitly barred from returning one. But Agent 1's
# `observed_address` is often a role account (it is the easiest real
# address to find on a company site), and resolve_address treats that
# field as a fallback address to draft *to*. On 2026-08-23 Agent 1
# returned support@company-d.dev as its observed address; passing it
# through would have put a cold email to a VP of Engineering into a support
# queue. Caught by hand that time. Now it cannot happen.
_ROLE_ACCOUNTS = frozenset({
    "info", "support", "hello", "hi", "contact", "careers", "jobs", "hiring",
    "admin", "sales", "press", "media", "legal", "help", "team", "office",
    "billing", "accounts", "ar", "ap", "security", "privacy", "abuse",
    "noreply", "no-reply", "donotreply", "postmaster", "webmaster",
})


def is_role_account(email: str) -> bool:
    """True for shared-inbox addresses, which must never be drafted to."""
    local = email.split("@", 1)[0].strip().lower()
    return local in _ROLE_ACCOUNTS or local.startswith(("no-reply", "noreply"))


_PATTERN_TOKENS = {
    "{first}": lambda f, l: f,
    "{last}": lambda f, l: l,
    "{f}": lambda f, l: f[:1],
    "{l}": lambda f, l: l[:1],
}


def _apply_pattern(pattern: str, first: str, last: str) -> str | None:
    """Render a Hunter pattern like '{first}.{last}' for a name."""
    if not pattern:
        return None
    local = pattern
    for token, render in _PATTERN_TOKENS.items():
        if token in local:
            local = local.replace(token, render(first.lower(), last.lower()))
    if "{" in local or "}" in local:
        return None  # a token we do not know how to render
    return local


def _name_conflict(
    emails: list[dict], address: str, first: str, last: str
) -> str | None:
    """None if `address` is safe to use for (first, last); otherwise a
    reason it is not.

    Hunter's domain-search enumerates real addresses it has seen, each with
    the name behind it when known. A rendered or observed address can
    coincide with one of those without belonging to the same person — that
    is exactly what happened with wrong.mailbox@company-c.io. This costs nothing
    extra: the name list is already in the domain-search response
    `confirm_pattern` fetches, just no longer discarded.
    """
    local = address.lower()
    for entry in emails:
        if (entry.get("value") or "").lower() != local:
            continue
        known_first = (entry.get("first_name") or "").strip()
        known_last = (entry.get("last_name") or "").strip()
        if not known_first:
            return None  # Hunter has the address but no name to check against
        if known_first.lower() == first.lower() and (
            not known_last or not last or known_last.lower() == last.lower()
        ):
            return None  # same person
        known = f"{known_first} {known_last}".strip()
        return (f"Hunter's domain-search lists {address} as {known}, "
                f"not {first} {last}")
    return None  # address isn't among Hunter's known contacts at all


def _roster_match(emails: list[dict], first: str, last: str) -> str | None:
    """The address Hunter itself attributes to this person, if it has one.

    The inverse of `_name_conflict`: instead of checking whether a rendered
    address belongs to someone else, look the person up directly in the
    domain roster. This is what rescues a contact when the domain has no
    usable pattern, or when the pattern renders somebody else's mailbox —
    the roster may still carry the intended person under a different local
    part.

    **Both last names must be present and equal.** A first-name-only match
    is how "[the contact]" gets handed [another employee]'s real, verifying mailbox — the exact
    wrong-person failure `_name_conflict` exists to catch, reintroduced
    through this rung where that check cannot see it. When either side
    lacks a surname, no match: the pattern and fallback rungs still run,
    and a missed rescue costs one skipped candidate where a false one
    costs an email to a stranger. Role accounts are excluded; a person
    matched to a shared inbox is a data error, not a contact.
    """
    for entry in emails:
        known_first = (entry.get("first_name") or "").strip().lower()
        known_last = (entry.get("last_name") or "").strip().lower()
        if not known_first or known_first != first.lower():
            continue
        if not last or not known_last or known_last != last.lower():
            continue
        value = (entry.get("value") or "").strip().lower()
        if value and not is_role_account(value):
            return value
    return None


def _candidate_addresses(
    pattern: str | None, emails: list[dict], first: str, last: str, domain: str
) -> tuple[list[tuple[str, str]], str]:
    """The ordered (address, source) attempts for one person, plus the
    reason the pattern rung was blocked when it was.

    This is the resolution ladder — pattern render guarded by the
    name-conflict check, then the roster lookup — extracted so that the
    advisory slate (`resolve_slate`) and the binding finalize resolution
    (`resolve_address`) can never disagree about it. Two parallel copies
    of the company-c safety logic drifting apart is how the human would
    end up approving one address while a different one gets drafted to.
    """
    attempts: list[tuple[str, str]] = []
    blocked = ""
    candidate = _apply_pattern(pattern or "", first, last)
    if candidate:
        address = f"{candidate}@{domain}"
        conflict = _name_conflict(emails, address, first, last)
        if conflict:
            blocked = conflict
        else:
            attempts.append((address, "pattern"))
    roster = _roster_match(emails, first, last)
    if roster and all(roster != address for address, _ in attempts):
        attempts.append((roster, "roster"))
    return attempts, blocked


# ── Apollo: a second address source, for the domains a probe cannot read ──
# Added 2026-09-03. The keyless probe's real gap is catch-all and firewalled
# domains -- measured live the same day: 7 of 8 companies above the spend
# bar in one unattended run's daily budget were exactly that shape (Kalshi,
# DeepL, TripleLift, GLG, Rocket Money, fuboTV, Industrious), and Hunter's
# free quota was dead, so all eight parked with no address. Apollo's
# person-enrichment endpoint (people/match) answers a different question
# than a probe can: not "does this mailbox exist" but "what is this named
# person's email, according to Apollo's own sourced data" -- so it works on
# a catch-all domain precisely where nothing that relies on the domain's
# own server can. Apollo's organization-search (a domain-wide roster, the
# thing that would replace confirm_pattern outright) is paid-only; person
# enrichment by name-plus-domain is on the free tier, credit-capped by
# account type. Written from the documented request/response shape
# (docs.apollo.io/reference/people-enrichment) and untested until a real
# key exists -- smoke-test with one known-good name at a known domain when
# the key is first added, the same way the ZeroBounce and MillionVerifier
# adapters were.
#
# Costs a real Apollo credit on every call that finds an email, so it is
# the last rung before giving up, after the free pattern, roster and probe
# rungs have all failed -- never a first choice just because it exists.
_APOLLO_MATCH_URL = "https://api.apollo.io/api/v1/people/match"

# Apollo's own verdict on the email it returns. Only a status this specific
# adapter recognizes as good becomes an address; anything else -- an
# unfamiliar value, a future API change -- degrades to a skip rather than
# trusting an unrecognized claim.
_APOLLO_GOOD_STATUS = frozenset({"verified", "likely to engage", "guessed"})


def _apollo_match(first: str, last: str, domain: str) -> tuple[str, dict] | str:
    """(email, raw) on a match Apollo stands behind, or a skip string.
    Cached per (domain, first, last) so a slate's alternates and a later
    finalize re-check never spend a second credit on the same person --
    checked before the key, like every other provider's cache, so a cached
    answer survives the key later being removed or rotated."""
    cache = _load_cache()
    cache_key = f"apollo:{domain}:{first.lower()}:{last.lower()}"
    if cache_key in cache:
        entry = cache[cache_key]
        if entry.get("email"):
            return entry["email"], {**entry.get("raw", {}), "cached": True}
        return f"apollo: {entry.get('reason', 'no match')} (cached)"
    load_dotenv(ENV_PATH)
    key = os.getenv("APOLLO_API_KEY")
    if not key:
        return "apollo: no APOLLO_API_KEY in .env"
    try:
        req = urllib.request.Request(
            _APOLLO_MATCH_URL,
            data=urllib.parse.urlencode({
                "first_name": first, "last_name": last, "domain": domain,
            }).encode(),
            headers={"x-api-key": key, "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", exc.reason)
        except Exception:
            detail = exc.reason
        return f"apollo: HTTP {exc.code}: {detail}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return f"apollo: {type(exc).__name__}: {exc}"

    person = payload.get("person") or {}
    email = person.get("email")
    status = (person.get("email_status") or "").lower()
    if not email or status not in _APOLLO_GOOD_STATUS:
        reason = f"no confident match (status={status or 'none'})"
        cache[cache_key] = {"email": None, "reason": reason, "cached_at": time.time()}
        _save_cache(cache)
        return f"apollo: {reason}"
    cache[cache_key] = {"email": email, "raw": {"email_status": status}, "cached_at": time.time()}
    _save_cache(cache)
    return email, {"email_status": status}


def _apollo_rung(
    emails: list[dict], first: str, last: str, domain: str
) -> tuple[str, VerificationResult] | str:
    """The last resolution rung: a real, Apollo-sourced email for the named
    person. Trusted as a verified address without an SMTP re-check --
    re-probing on a catch-all domain would only get "catch_all" back and
    throw away the one thing Apollo knew that a probe cannot. Still runs
    the same wrong-person guard the pattern and roster rungs do."""
    matched = _apollo_match(first, last, domain)
    if isinstance(matched, str):
        return matched
    address, raw = matched
    conflict = _name_conflict(emails, address, first, last)
    if conflict:
        return conflict
    return address, VerificationResult(
        email=address, label=VERIFIED,
        reason=f"Apollo matched {first} {last} to this address "
               f"(status: {raw.get('email_status', 'unknown')})",
        raw=raw,
    )


def resolve_address(
    first: str, last: str, domain: str, *, fallback: str | None = None
) -> tuple[str | None, VerificationResult]:
    """Find this person's address at a domain, using Hunter's domain-wide
    pattern rather than a single observed address.

    Tries, in order: the domain pattern rendered for the name, the roster
    address Hunter attributes to the person directly, then `fallback`.
    Costs at most one domain-search (cached per domain) plus one
    verification per attempted address. Returns (address, verification).
    """
    pattern, note, emails = confirm_pattern(domain)
    attempts, blocked = _candidate_addresses(pattern, emails, first, last, domain)

    # Which rung failed matters: reporting a dead mailbox as "no usable
    # pattern" sent me chasing the wrong bug once.
    tried = [blocked] if blocked else []
    for address, source in attempts:
        result = verify_email(address)
        if not result.should_block:
            return address, result
        tried.append(f"{source} address {address} does not exist "
                     f"({result.reason})")

    # Keyless rung: the domain's own server stands in for the roster.
    probed = _probe_rung(emails, first, last, domain)
    if isinstance(probed, str):
        tried.append(probed)
    else:
        return probed

    # Apollo rung: costs a real credit, so only reached once every free
    # rung (pattern, roster, probe) has already failed.
    matched = _apollo_rung(emails, first, last, domain)
    if isinstance(matched, str):
        tried.append(matched)
    else:
        return matched

    if fallback and is_role_account(fallback):
        return None, VerificationResult(
            email=fallback,
            label=UNVERIFIED,
            reason=f"refusing role account {fallback} as a fallback target; "
                   f"no personal address could be derived for {domain}",
        )

    if fallback:
        conflict = _name_conflict(emails, fallback, first, last)
        if conflict:
            return None, VerificationResult(
                email=fallback, label=UNVERIFIED, reason=conflict,
            )
        result = verify_email(fallback)
        if not result.should_block:
            return fallback, result
        return None, result

    if tried:
        return None, VerificationResult(
            email=f"?@{domain}", label=UNVERIFIED, reason="; ".join(tried)
        )
    return None, VerificationResult(
        email=f"?@{domain}",
        label=UNVERIFIED,
        reason=f"no usable pattern for {domain} ({pattern or 'none'}; {note})",
    )


# ── Slate resolution ───────────────────────────────────────────────────────
# Agent 1 returns a ranked slate of candidates rather than one committed
# pick (docs/research/contact-strategy-findings.md). Before the slate is
# shown to the human, every candidate gets an address resolved against the
# domain's single cached roster, but only the first deliverable one costs a
# verification credit — the point is to know which picks are reachable
# before drafting, not to spend the month's quota confirming alternates
# nobody chose. resolve_address() remains the authority for whichever
# candidate the human actually selects.

DEFERRED = "deferred"  # resolvable, deliberately not verified yet


@dataclass
class SlateResolution:
    name: str
    address: str | None
    source: str  # 'pattern' | 'roster' | ''
    label: str   # a verification label, or DEFERRED when no credit was spent
    score: int | None
    reason: str


def resolve_slate(
    names: list[str], domain: str, *, fallback: str | None = None
) -> list[SlateResolution]:
    """Resolve an address for each candidate name at one domain.

    One domain-search (cached) covers the whole slate, and every candidate
    walks the same `_candidate_addresses` ladder finalize uses, so the
    preview cannot disagree with the binding resolution. Verification
    credits are spent only until the first candidate proves deliverable;
    everyone after that is reported as DEFERRED with the address that
    would be tried.

    `fallback` is Agent 1's company-level observed address. It is never
    attributed to a candidate here — showing the same address under three
    names would be exactly the misattribution this module exists to
    prevent — but an unresolved candidate's reason notes that finalize
    can still try it, so the preview does not under-report reachability
    at the small companies where the fallback is the only route.
    """
    pattern, note, emails = confirm_pattern(domain)
    fallback_note = ""
    if fallback and not is_role_account(fallback):
        fallback_note = (f"; finalize can still try the observed address "
                         f"{fallback} if this candidate is chosen")

    out: list[SlateResolution] = []
    have_verified = False
    for name in names:
        parts = (name or "").split()
        if not parts:
            # One malformed agent-emitted candidate must cost one slate
            # row, never the whole verify-slate call.
            out.append(SlateResolution(
                name or "", None, "", UNVERIFIED, None,
                "candidate has no name",
            ))
            continue
        first, last = parts[0], (parts[-1] if len(parts) > 1 else "")

        attempts, blocked = _candidate_addresses(
            pattern, emails, first, last, domain
        )
        if not attempts:
            # Probing is free, so every unresolved candidate gets the
            # keyless rung regardless of whether an earlier one verified.
            probed = _probe_rung(emails, first, last, domain)
            if not isinstance(probed, str):
                address, result = probed
                out.append(SlateResolution(
                    name, address, "probe", result.label, None, result.reason,
                ))
                continue
            reason_so_far = (blocked + "; " if blocked else "") + probed
            # Apollo costs a credit: only the first still-unresolved
            # candidate in the slate may spend it, same discipline as
            # have_verified already applies to paid verification below.
            if have_verified:
                out.append(SlateResolution(
                    name, None, "", DEFERRED, None,
                    "not tried yet; an Apollo lookup runs if this candidate "
                    "is chosen" + fallback_note,
                ))
                continue
            matched = _apollo_rung(emails, first, last, domain)
            if isinstance(matched, str):
                out.append(SlateResolution(
                    name, None, "", UNVERIFIED, None,
                    reason_so_far + "; " + matched + fallback_note,
                ))
            else:
                address, result = matched
                have_verified = True
                out.append(SlateResolution(
                    name, address, "apollo", result.label, None, result.reason,
                ))
            continue

        if have_verified:
            address, source = attempts[0]
            out.append(SlateResolution(
                name, address, source, DEFERRED, None,
                "not verified yet; finalize verifies it if chosen",
            ))
            continue

        last_failure: tuple[str, str, VerificationResult] | None = None
        for address, source in attempts:
            result = verify_email(address)
            if not result.should_block:
                have_verified = True
                out.append(SlateResolution(
                    name, address, source, result.label, result.score,
                    result.reason,
                ))
                break
            last_failure = (address, source, result)
        else:
            # attempts was non-empty (guarded above), so last_failure is set.
            address, source, result = last_failure
            out.append(SlateResolution(
                name, None, source, result.label, result.score,
                f"{address} does not exist ({result.reason})" + fallback_note,
            ))

    return out


# ── Keyless resolution ─────────────────────────────────────────────────────
# When Hunter's search quota is gone — every counter on the free plan hit
# zero by 2026-08-28 and again by 2026-09-02; it resets on the 11th —
# `confirm_pattern` returns nothing, and a candidate at a company with no
# observed personal address is unresolvable. Three researched companies
# produced zero drafts on 2026-09-01 for exactly this reason. The domain's
# own mail server can stand in for the roster on every domain that rejects
# unknown recipients: render the conventional patterns for the name, ask
# the server which one exists, take it. On a catch-all domain nothing
# keyless works (the server says yes to everything) and Hunter's roster is
# still the only route.
#
# Identity is the risk, exactly as with Hunter's pattern render, and it is
# handled by tiering the patterns rather than by pretending the risk away:
#
#   full-name patterns (first.last, firstlast, ...) put the whole name in
#     the address. A hit is identity-bound and keeps the probe's label.
#   partial patterns ({first}, {f}{last}, {first}{l}, ...) would match a
#     namesake as readily as the intended person. A hit is labeled RISKY
#     with the reason spelled out, so the review card says "confirm the
#     person" instead of "verified". They cannot be dropped: {first} is the
#     convention at 12 of the 18 domains in the roster cache, which is the
#     small-company norm this pipeline lives in. The company-c wrong-person
#     case (2026-08-24) was a {first}{l} render labeled verified — the
#     label is the fix, not the exclusion.
#
# When a Hunter roster is cached for the domain, `_name_conflict` still
# runs against the probed address, same as for a pattern render. Every
# probed address is cached with its verdict, so finalize's re-verification
# of a probed pick is a cache hit and re-running a slate costs nothing.

_FULL_NAME_PATTERNS = ("{first}.{last}", "{first}{last}", "{first}_{last}",
                       "{last}.{first}", "{first}-{last}")
_PARTIAL_NAME_PATTERNS = ("{first}", "{f}{last}", "{f}.{last}", "{first}{l}",
                          "{first}.{l}", "{last}")


@dataclass
class ProbeResolution:
    address: str | None
    pattern: str = ""
    partial: bool = False
    reason: str = ""
    probed: dict = field(default_factory=dict)  # address -> verdict


def _name_token(name: str) -> str:
    """ASCII, lowercase, letters and digits only: O'Brien -> obrien,
    José -> jose, Van Der Berg -> vanderberg."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _pattern_plan(first: str, last: str, domain: str) -> list[tuple[str, str, bool]]:
    """(address, pattern, partial) in the order they should be tried."""
    f, l = _name_token(first), _name_token(last)
    if not f:
        return []
    tiers = ([(False, _FULL_NAME_PATTERNS), (True, _PARTIAL_NAME_PATTERNS)]
             if l else [(True, ("{first}",))])
    plan, seen = [], set()
    for partial, patterns in tiers:
        for pattern in patterns:
            local = _apply_pattern(pattern, f, l)
            if not local:
                continue
            address = f"{local}@{domain}"
            if address not in seen:
                seen.add(address)
                plan.append((address, pattern, partial))
    return plan


def _first_hit(plan, probed) -> tuple[str, str, bool] | None:
    for address, pattern, partial in plan:
        if probed.get(address) == VERIFIED:
            return address, pattern, partial
    return None


def _resolution(plan, probed, domain: str, notes: list[str]) -> ProbeResolution:
    hit = _first_hit(plan, probed)
    if not hit:
        return ProbeResolution(
            None, reason=(f"keyless: none of {len(plan)} conventional patterns exist at "
                          f"{domain}" + ("; " + "; ".join(notes) if notes else "")),
            probed=probed,
        )
    address, pattern, partial = hit
    others = [a for a, v in probed.items() if v == VERIFIED and a != address]
    if partial:
        reason = (f"keyless: {pattern} exists at {domain} but only part of the name "
                  f"is in it, so a namesake would match too — confirm the person "
                  f"before sending")
    else:
        reason = f"keyless: {pattern} exists at {domain} and carries the full name"
    if others:
        reason += f"; also exists: {', '.join(others)}"
    return ProbeResolution(address, pattern, partial, reason, probed)


def probe_patterns(
    first: str, last: str, domain: str, *, use_cache: bool = True
) -> ProbeResolution:
    """Which conventional address for this name exists at the domain,
    according to the domain's own mail server. One SMTP session, at most
    a dozen RCPT TOs, no vendor, no credit. Stops at the first full-name
    hit; probes every partial pattern so the reason can say when more than
    one exists. Returns address=None on a catch-all domain, when the
    server will not answer, or when nothing exists."""
    plan = _pattern_plan(first, last, domain)
    if not plan:
        return ProbeResolution(None, reason="keyless: no name to render")
    cache = _load_cache()
    catch_all = _catch_all_cached(cache, domain) if use_cache else None
    if catch_all:
        return ProbeResolution(
            None, reason=(f"keyless: {domain} accepts all mail, so no probe can tell "
                          f"one mailbox from another (needs Hunter's roster)"),
        )
    probed: dict[str, str] = {}
    pending = []
    for address, pattern, partial in plan:
        entry = cache.get(address) if use_cache else None
        if entry and entry.get("label") in (VERIFIED, INVALID):
            probed[address] = entry["label"]
        else:
            pending.append((address, pattern, partial))
    hit = _first_hit(plan, probed)
    if (hit and not hit[2]) or not pending:
        return _resolution(plan, probed, domain, [])

    session = _open_probe_session(domain)
    if isinstance(session, str):
        return ProbeResolution(None, reason="keyless: " + session, probed=probed)
    host, server = session
    notes: list[str] = []
    try:
        if catch_all is None:
            if _accepts_random(server, domain, {}):
                _remember_catch_all(cache, domain, True)
                return ProbeResolution(
                    None, reason=(f"keyless: {domain} accepts all mail, so no probe can "
                                  f"tell one mailbox from another (needs Hunter's roster)"),
                    probed=probed,
                )
            _remember_catch_all(cache, domain, False)
        for address, pattern, partial in pending:
            label, raw = _rcpt_verdict(server, address)
            if label is None:
                notes.append(f"{address}: no verdict ({raw['code']} {raw['enhanced']})")
                if 400 <= raw["code"] < 500:
                    notes.append("server asked to slow down; stopped probing")
                    break
                continue
            probed[address] = label
            if label in (VERIFIED, INVALID):
                cache[address] = {
                    "label": label, "score": None,
                    "reason": (f"SMTP-confirmed deliverable by {host}" if label == VERIFIED
                               else f"{host} reports no such mailbox ({raw['code']} {raw['enhanced']})")
                              + " (via smtp)",
                    "provider": "smtp", "raw": {"mx": host, **raw},
                }
            if label == VERIFIED and not partial:
                break
        _save_cache(cache)
    except (OSError, smtplib.SMTPException) as exc:
        notes.append(f"session with {host} failed ({type(exc).__name__}: {exc})")
    finally:
        _quietly_quit(server)
    return _resolution(plan, probed, domain, notes)


def _probe_rung(
    emails: list[dict], first: str, last: str, domain: str
) -> tuple[str, VerificationResult] | str:
    """The keyless step of the resolution ladder, shared by resolve_address
    and resolve_slate so the two can never disagree about it. Returns
    (address, verification) or a string saying why not."""
    probe = probe_patterns(first, last, domain)
    if not probe.address:
        return probe.reason
    conflict = _name_conflict(emails, probe.address, first, last)
    if conflict:
        return conflict
    result = verify_email(probe.address)  # cache hit from the probe itself
    if result.should_block:
        return f"keyless: {probe.address} was refused on re-check ({result.reason})"
    if probe.partial:
        return probe.address, VerificationResult(
            email=probe.address, label=RISKY, score=None,
            reason=probe.reason, raw=result.raw,
        )
    result.reason = f"{probe.reason}; {result.reason}"
    return probe.address, result
