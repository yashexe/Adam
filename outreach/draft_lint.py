"""
Deterministic checks on a draft before it reaches Gmail.

Agent 2 kept writing dense, mechanism-heavy prose. On 2026-08-23 the
density rule was tightened twice and pre-compressed "In an email" lines
were authored for every story in PROFILE.md, and the drafter still
produced "I split our dispatcher and executor queues and made every job
idempotent with Redis locks, which cut processing time 87%" when the line
it was told to use was "I rebuilt how our jobs run and cut processing time
87%."

A model asked to hold back technical detail, while looking at technical
detail, will drift back to it. So this stops being a prompting problem and
becomes a check: the same shape as stage 4 verifying Agent 1 rather than
trusting it. Deterministic code owns the standard, the model does the
writing.

    from outreach.draft_lint import lint
    issues = lint(body)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 24, not 20. The cap exists to stop dense prose, and length alone is a
# poor proxy for it -- Yash's own model opener ("I noticed that you guys
# posted the ___ role recently and I thought it'd be a great opportunity to
# email you!") runs 23 words and is not dense at all. The clause-pileup
# check below is what actually catches density; this is a backstop for
# sentences that have plainly run away.
MAX_SENTENCE_WORDS = 24
# The résumé is attached to every email, so the body carries no burden of
# proof. One number, and only when it is the point.
MAX_NUMBERS = 1
MAX_MECHANISM_TERMS = 3
MAX_BULLET_WORDS = 14

# Vocabulary that describes how a system works rather than what it does.
# Not banned outright: one is fine, a pile of them is the failure.
_MECHANISM = [
    "idempoten", "dispatcher", "executor", "distributed lock", "redis lock",
    "queue", "backoff", "retry", "reconcil", "deterministic", "schema",
    "constrained decoding", "roll-up", "rollup", "mtls", "oauth", "odbc",
    "pagination", "cursor", "throughput", "concurrency", "orchestrat",
    "fan-out", "fanout", "webhook", "autoscal", "encryption", "multi-tenant",
]
# Trailing qualifiers that show up in nearly every draft and read as padding.
_TIC = re.compile(
    r",\s*(built from scratch|from scratch|end to end|from the ground up|"
    r"end-to-end)\b", re.IGNORECASE)
_BANNED = {
    "commits": "commit counts mean nothing to a stranger",
    "lines of code": "internal metric",
    "codebase": "internal metric",
}
# Splits on sentence-ending punctuation; good enough for short emails.
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_NUMBER = re.compile(r"\b\d[\d,.]*\+?[KMX%]?\b", re.IGNORECASE)
_SCALE_VERB = re.compile(
    r"\b(process(es|ing)?|handl(es|ing)|serv(es|ing)|support(s|ing)?|"
    r"scal(ed|ing)|across)\b", re.IGNORECASE)


# How long an email may be, by who opens it. A non-engineering executive
# reading four sentences is the design; the same content at eight sentences
# is a different email that will not get read. Sentence count is also the
# only mechanical proxy for "one story per email" that works: four
# sentences cannot hold two stories.
TIER_MAX_SENTENCES = {
    "exec": 4,        # VP Finance, COO, CFO, Head of Ops
    "recruiter": 4,
    "founder": 5,     # founder or CEO at a small company
    "eng_leader": 6,  # CTO, VP Engineering, Head of Engineering
    "ic": 7,          # senior or staff individual contributor
}

_TIER_PATTERNS = [
    ("recruiter", r"recruit|talent|sourc|people ops|staffing"),
    ("exec", r"\b(cfo|coo|chief financial|chief operating|vp of finance|"
             r"finance systems|head of (finance|operations|ops)|controller)\b"),
    ("eng_leader", r"\b(cto|chief technology|vp of engineering|vp engineering|"
                   r"head of engineering|director of engineering|"
                   r"engineering manager|head of platform|head of data)\b"),
    ("founder", r"founder|chief executive|\bceo\b"),
    ("ic", r"engineer|developer|architect|technical staff|\bswe\b"),
]


def tier_for_role(role: str) -> str:
    """Best-effort recipient tier from a free-text job title.

    Order matters: a "Founding Engineer" is an IC, a "CTO & Co-Founder" is
    an engineering leader, and "VP of Finance Systems" is an executive even
    though the title contains a systems word.
    """
    text = (role or "").lower()
    for tier, pattern in _TIER_PATTERNS:
        if re.search(pattern, text):
            return tier
    return "eng_leader"


@dataclass
class Issue:
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.detail}"


def lint(body: str, *, role: str | None = None, tier: str | None = None) -> list[Issue]:
    """Everything wrong with this draft. Empty list means it passes.

    Pass `role` (the recipient's job title) or `tier` directly to enforce
    the length ceiling for that audience. Without either, only the
    audience-independent checks run.
    """
    issues: list[Issue] = []
    text = body.strip()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        is_bullet = line.startswith(("-", "*", "•"))
        content = line.lstrip("-*• ").strip()

        if is_bullet and len(content.split()) > MAX_BULLET_WORDS:
            issues.append(Issue(
                "bullet too long",
                f"{len(content.split())} words (max {MAX_BULLET_WORDS}): {content[:60]}...",
            ))
            continue

        for sentence in _SENTENCE.split(content):
            words = sentence.split()
            if len(words) > MAX_SENTENCE_WORDS:
                issues.append(Issue(
                    "sentence too long",
                    f"{len(words)} words (max {MAX_SENTENCE_WORDS}): {sentence[:70]}...",
                ))
            # Two or more subordinate clauses stacked is the pileup shape.
            commas = sentence.count(",")
            if commas >= 2 and len(words) > 14:
                issues.append(Issue(
                    "clause pileup",
                    f"{commas} commas in one sentence: {sentence[:70]}...",
                ))

    numbers = _NUMBER.findall(text)
    if len(numbers) > MAX_NUMBERS:
        issues.append(Issue(
            "too many numbers",
            f"{len(numbers)} found ({', '.join(numbers)}); the résumé is "
            f"attached and already carries these. Show impact instead.",
        ))

    # A sentence that is only scale, usually pasted in at the end.
    for sentence in _SENTENCE.split(text.replace("\n", " ")):
        if _NUMBER.search(sentence) and _SCALE_VERB.search(sentence):
            issues.append(Issue(
                "scale recital",
                f"reads as a résumé line: {sentence.strip()[:70]}...",
            ))

    lowered = text.lower()
    hits = sorted({term for term in _MECHANISM if term in lowered})
    if len(hits) > MAX_MECHANISM_TERMS:
        issues.append(Issue(
            "mechanism-heavy",
            f"{len(hits)} implementation terms: {', '.join(hits)}. "
            f"Say what it does, not how it works.",
        ))

    for term, why in _BANNED.items():
        if term in lowered:
            issues.append(Issue("banned", f"{term!r}: {why}"))

    for match in _TIC.finditer(text):
        issues.append(Issue(
            "tacked-on qualifier",
            f"{match.group(0).strip()!r} appended to a clause; give it its "
            f"own sentence or drop it",
        ))

    if "—" in text or "–" in text:
        issues.append(Issue("banned", "em or en dash"))

    resolved = tier or (tier_for_role(role) if role else None)
    if resolved:
        limit = TIER_MAX_SENTENCES.get(resolved)
        body_text = _strip_greeting_and_ask(text)
        sentences = [s for s in _SENTENCE.split(body_text) if s.strip()]
        if limit and len(sentences) > limit:
            issues.append(Issue(
                "too long for recipient",
                f"{len(sentences)} body sentences, max {limit} for a "
                f"{resolved}. Cut to one story.",
            ))

    return issues


def _strip_greeting_and_ask(text: str) -> str:
    """Body only: drop the greeting line and the closing ask."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines and re.match(r"^(hi|hey|hello)\b|^[A-Z][a-z]+,$", lines[0], re.I):
        lines = lines[1:]
    if lines and re.search(r"\b(chat|call|minutes|free|around)\b", lines[-1], re.I):
        lines = lines[:-1]
    return " ".join(lines)


def report(body: str, *, role: str | None = None, tier: str | None = None) -> str:
    issues = lint(body, role=role, tier=tier)
    if not issues:
        return "clean"
    return "\n".join(f"  {i}" for i in issues)
