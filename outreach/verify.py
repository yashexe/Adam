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
- **Mailbox probing** is a commodity and walks `_VERIFY_PROVIDERS` —
  free-tier providers first (ZeroBounce, MillionVerifier; each activates
  when its key is in `.env`), Hunter last. One 12-company drain run
  (2026-08-28) consumed a whole Hunter month mostly on probes; this split
  is what lets the free tiers cover a real monthly volume.

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
import time
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
# plain verification walks this chain instead — free-tier providers first,
# Hunter last. Each adapter activates only when its key is in .env, returns
# a VerificationResult on an answer, or a string saying why it must be
# skipped (no key, quota gone, API error) so the next provider gets a turn.
# Every provider normalizes onto the same five labels; `should_block`
# semantics are identical regardless of who answered. Added 2026-08-29,
# after one 12-company drain run consumed the entire Hunter month.
#
# Adapters are written from each provider's documented v2/v3 API shape but
# land untested until a real key exists — smoke-test with one known-good
# address when a key is first added, and expect the defensive UNVERIFIED
# path on any surprise rather than a crash.

def _verify_via_zerobounce(email: str) -> VerificationResult | str:
    """https://api.zerobounce.net/v2/validate — free tier ~100/month."""
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
    label, reason = {
        "valid": (VERIFIED, "SMTP-confirmed deliverable"),
        "invalid": (INVALID, "ZeroBounce reports invalid"),
        "catch-all": (CATCH_ALL, "domain accepts all mail; mailbox unconfirmable"),
        # A spamtrap is a real, deliverable mailbox that exists to burn
        # sender reputation — blocking is the only sane response.
        "spamtrap": (INVALID, "ZeroBounce flags a spamtrap"),
        "abuse": (RISKY, "ZeroBounce flags an abuse-prone address"),
        "do_not_mail": (RISKY, "ZeroBounce reports do_not_mail"),
        "unknown": (RISKY, "ZeroBounce could not conclude"),
    }.get(status, (UNVERIFIED, f"unrecognized ZeroBounce status ({status or 'empty'})"))
    return VerificationResult(email=email, label=label, score=None,
                              reason=reason, raw=payload)


def _verify_via_millionverifier(email: str) -> VerificationResult | str:
    """https://api.millionverifier.com/api/v3 — pay-as-you-go with free credits."""
    load_dotenv(ENV_PATH)
    key = os.getenv("MILLIONVERIFIER_API_KEY")
    if not key:
        return "millionverifier: no MILLIONVERIFIER_API_KEY"
    payload, error = _get("https://api.millionverifier.com/api/v3/",
                          {"api": key, "email": email})
    if payload is None:
        return f"millionverifier: {error}"
    if payload.get("error"):
        return f"millionverifier: {payload['error']}"
    result = (payload.get("result") or "").lower()
    label, reason = {
        "ok": (VERIFIED, "SMTP-confirmed deliverable"),
        "invalid": (INVALID, "MillionVerifier reports invalid"),
        "disposable": (INVALID, "disposable address domain"),
        "catch_all": (CATCH_ALL, "domain accepts all mail; mailbox unconfirmable"),
        "unknown": (RISKY, "MillionVerifier could not conclude"),
    }.get(result, (UNVERIFIED, f"unrecognized MillionVerifier result ({result or 'empty'})"))
    return VerificationResult(email=email, label=label, score=None,
                              reason=reason, raw=payload)


def _verify_via_hunter(email: str) -> VerificationResult | str:
    key = _api_key()
    if not key:
        return f"hunter: no HUNTER_API_KEY in {ENV_PATH}"
    payload, error = _get(VERIFIER_URL, {"email": email, "api_key": key})
    if payload is None:
        return f"hunter: {error}"
    data = payload.get("data") or {}
    label, reason = _label_from(data)
    return VerificationResult(email=email, label=label,
                              score=data.get("score"), reason=reason, raw=data)


_VERIFY_PROVIDERS = (
    ("zerobounce", _verify_via_zerobounce),
    ("millionverifier", _verify_via_millionverifier),
    ("hunter", _verify_via_hunter),
)


def verify_email(email: str, *, use_cache: bool = True) -> VerificationResult:
    """Check one address via the first provider able to answer.

    Spends one credit at whichever provider answers, unless cached. With no
    alternate keys configured this behaves exactly as the Hunter-only
    version did.
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
    for name, provider in _VERIFY_PROVIDERS:
        outcome = provider(email)
        if isinstance(outcome, str):
            skips.append(outcome)
            continue
        reason = outcome.reason if name == "hunter" else f"{outcome.reason} (via {name})"
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
            out.append(SlateResolution(
                name, None, "", UNVERIFIED, None,
                (blocked or f"no pattern or roster entry at {domain} "
                            f"({pattern or 'no pattern'}; {note})")
                + fallback_note,
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
