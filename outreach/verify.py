"""
Stage 4 — deterministic email verification.

Sits between Agent 1 and Agent 2 on purpose. Agent 1 produces a candidate
address by inferring a pattern from public evidence; this step checks that
address against Hunter.io and reduces everything it learns to a single
label. Agent 2 receives the label and the address and nothing else — not
the SMTP details, not Hunter's raw JSON, not Agent 1's source notes.
Restricting what reaches the model is the point, not just gating its
output.

Nothing here raises into the pipeline. An unreachable API, a missing key,
or an exhausted quota all degrade to `UNVERIFIED`, which is honest and lets
a human decide. A verification step that can halt the pipeline is worse
than one that says "I don't know".

Credits: the free tier is 50/month and the verifier spends one per address.
`verify_email` caches by address so re-running the pipeline over the same
company is free.

    from outreach.verify import verify_email
    result = verify_email("constructed.guess@company-a.com")
    result.label, result.should_block
"""

from __future__ import annotations

import json
import os
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


def verify_email(email: str, *, use_cache: bool = True) -> VerificationResult:
    """Check one address. Spends one Hunter credit unless cached."""
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

    key = _api_key()
    if not key:
        return VerificationResult(
            email=email,
            label=UNVERIFIED,
            reason=f"no HUNTER_API_KEY in {ENV_PATH}",
        )

    payload, error = _get(VERIFIER_URL, {"email": email, "api_key": key})
    if payload is None:
        return VerificationResult(email=email, label=UNVERIFIED, reason=error)

    data = payload.get("data") or {}
    label, reason = _label_from(data)
    result = VerificationResult(
        email=email, label=label, score=data.get("score"), reason=reason, raw=data
    )

    cache[email] = {
        "label": label,
        "score": data.get("score"),
        "reason": reason,
        "raw": data,
    }
    _save_cache(cache)
    return result


def confirm_pattern(domain: str) -> tuple[str | None, str]:
    """Hunter's own view of a domain's address pattern, for corroborating
    what Agent 1 inferred. Spends a credit, so this is opt-in: use it when
    Agent 1's pattern evidence was weak, not on every contact."""
    key = _api_key()
    if not key:
        return None, f"no HUNTER_API_KEY in {ENV_PATH}"
    payload, error = _get(DOMAIN_SEARCH_URL, {"domain": domain, "api_key": key})
    if payload is None:
        return None, error
    data = payload.get("data") or {}
    return data.get("pattern"), f"{len(data.get('emails') or [])} addresses seen"


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
    return available, f"used {used} of {available}" if available is not None else "unknown"


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


def resolve_address(
    first: str, last: str, domain: str, *, fallback: str | None = None
) -> tuple[str | None, VerificationResult]:
    """Find this person's address at a domain, using Hunter's domain-wide
    pattern rather than a single observed address.

    Costs up to 3 credits: one domain-search, then a verification of the
    pattern-derived address, and one more if `fallback` differs and the
    first came back blocking. Returns (address, verification).
    """
    pattern, note = confirm_pattern(domain)
    candidate = _apply_pattern(pattern or "", first, last)

    tried = ""
    if candidate:
        address = f"{candidate}@{domain}"
        result = verify_email(address)
        if not result.should_block:
            return address, result
        # The pattern was fine, the resulting mailbox just is not there.
        # Reporting this as "no usable pattern" sent me chasing the wrong
        # bug once; say which of the two actually failed.
        tried = (f"pattern {pattern} gives {address}, which does not exist "
                 f"({result.reason})")

    if fallback and is_role_account(fallback):
        return None, VerificationResult(
            email=fallback,
            label=UNVERIFIED,
            reason=f"refusing role account {fallback} as a fallback target; "
                   f"no personal address could be derived for {domain}",
        )

    if fallback:
        result = verify_email(fallback)
        if not result.should_block:
            return fallback, result
        return None, result

    if tried:
        return None, VerificationResult(
            email=f"?@{domain}", label=UNVERIFIED, reason=tried
        )
    return None, VerificationResult(
        email=f"?@{domain}",
        label=UNVERIFIED,
        reason=f"no usable pattern for {domain} ({pattern or 'none'}; {note})",
    )
