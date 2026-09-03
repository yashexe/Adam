"""
Candidate postings, read from the live tracker on the Pi.

ashby-ny-tracker's `tracker.db` is the only copy that matters and it lives
at /home/pi/ashby-ny-tracker/tracker.db (the checkout on this Mac has its
own stale `tracker.db` — do not read that one). Access is strictly
read-only over SSH via `?mode=ro`, the pattern PI.md already uses for
health checks, and this stage writes nothing back. Read-only was not
lock-free, though: until 2026-09-03 tracker.db was in rollback-journal
mode, so this query held a SHARED lock for its 2-6 s (an unindexed scan
of `seen_jobs`) and the poll's commit gave up after 5 s -- two polls
crashed with "database is locked" that morning under back-to-back reads
from here. Fixed on the Pi the same day (tracker commit aa42c00): WAL
mode, so a reader never blocks the poll's commit; a 30 s lock timeout as
backstop; and an index on `first_seen_at`. The tick is still pinned to
:02:30 of each slot as belt and braces, not because a collision would
break anything now.

`seen_jobs` records every posting from every board, not just matches — the
NY/role/freshness filter is applied to `pending_alerts`, which is cleared
the moment the alert email goes out (run.py:244) and so cannot serve as a
queue. The predicates below are therefore reproduced from poll.py to
reconstruct "what the tracker would have alerted on" from the durable
table.
"""

from __future__ import annotations

import json
import subprocess

# mDNS first, LAN IP second: the Pi's WiFi flap sometimes takes down its
# .local advertisement while SSH by address still works (observed
# 2026-08-28). The IP is a DHCP lease, so it stays the fallback, not the
# name of record.
PI_HOSTS = ("pi@raspberrypi.local", "pi@10.0.0.147")
PI_PYTHON = "~/ashby-ny-tracker/.venv/bin/python3"
PI_DB = "/home/pi/ashby-ny-tracker/tracker.db"

# Verbatim from ashby-ny-tracker/src/poll.py. Kept in sync by hand: this is
# a copy, not an import, because reaching across projects for two regexes
# would couple this pipeline to the tracker's internals.
_REMOTE_SCRIPT = r'''
import json, re, sqlite3, sys

NY_KEYWORDS = ("new york", "nyc", "manhattan", "brooklyn", "queens", "bronx",
               "staten island", "long island city")
NY_RE = re.compile(r"\b(?:" + "|".join(NY_KEYWORDS) + r")\b")
ROLE_KEYWORDS = ("engineer", "engineering", "developer", "architect", "programmer",
                 "software", "technical", "devops", "sre", "site reliability",
                 "integration", "forward deployed", "fde", "swe", "sde", "mts",
                 "machine learning", "ai", "data scientist", "data science",
                 "scientist", "quantitative", "quant", "deployment",
                 "implementation", "solutions", "solution", "systems",
                 "chief technology officer", "cto")
ROLE_RE = re.compile(r"\b(?:" + "|".join(ROLE_KEYWORDS) + r")\b")

days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
conn = sqlite3.connect("file:DB_PATH?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT platform, job_id, company_slug, title, location, url, funding_hint, "
    "first_seen_at FROM seen_jobs WHERE first_seen_at >= datetime('now', ?) "
    "ORDER BY first_seen_at DESC",
    (f"-{days} day",),
).fetchall()
role_filter = (sys.argv[2] if len(sys.argv) > 2 else "1") == "1"
out = [
    dict(r) for r in rows
    if NY_RE.search((r["location"] or "").lower())
    and (not role_filter or ROLE_RE.search((r["title"] or "").lower()))
]
print(json.dumps(out))
'''


def fetch_candidates(
    days: int = 7, limit: int | None = None, *, role_filter: bool = True
) -> list[dict]:
    """Postings the tracker saw in the last `days` days.

    With `role_filter=True` (the default) this reproduces what the tracker
    would have alerted on. With it False, only the NY location predicate
    applies -- every NY posting seen, regardless of title.
    """
    script = _REMOTE_SCRIPT.replace("DB_PATH", PI_DB)
    errors = []
    for host in PI_HOSTS:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
             host, f"{PI_PYTHON} - {days} {'1' if role_filter else '0'}"],
            input=script, capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            rows = json.loads(result.stdout)
            return rows[:limit] if limit else rows
        errors.append(f"{host}: {result.stderr.strip()}")
    raise RuntimeError(
        "could not read the tracker DB on any known host "
        "(the Pi's WiFi is known to flap; see PI.md):\n" + "\n".join(errors)
    )
