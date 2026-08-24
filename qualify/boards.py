"""
Job descriptions, fetched from the public board APIs.

`seen_jobs` stores only what the tracker derived from a posting
(funding_hint, comp_summary) — never the description itself. Without the
description the scorer loses required_skills_fit, experience_fit, the
preferred-skills bonus, and most of domain_company_fit: four of its seven
dimensions, and every dimension that actually discriminates between two NY
engineering postings. So the text has to be re-fetched.

Both platform modules in the tracker already pull it (ashby_api.py asks for
descriptions to derive funding_hint; greenhouse_api.py passes content=true),
which confirms these endpoints carry it. Fetched directly from the public
APIs here rather than through the Pi — same data, one less hop, and no
contention with the poll cycle.

Boards are cached per company: a board response covers every posting at
that company, and several qualifying postings from one company is the
common case.
"""

from __future__ import annotations

import html
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "boards"
CACHE_TTL_SECONDS = 24 * 60 * 60
_TIMEOUT = 20
_USER_AGENT = "outreach-agent-qualify/0.1"

ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(raw: str) -> str:
    """Greenhouse returns entity-encoded HTML; unescape before stripping or
    the tags survive as literal &lt;p&gt; text."""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html.unescape(raw or ""))).strip()


def _cache_path(platform: str, slug: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", slug)
    return CACHE_DIR / f"{platform}__{safe}.json"


def fetch_board(platform: str, slug: str, *, refresh: bool = False) -> dict | None:
    """Whole-board response for one company, cached. None if unreachable."""
    path = _cache_path(platform, slug)
    if not refresh and path.exists():
        if time.time() - path.stat().st_mtime < CACHE_TTL_SECONDS:
            try:
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                pass  # corrupt cache entry — refetch below

    template = {"ashby": ASHBY_API, "greenhouse": GREENHOUSE_API}.get(platform)
    if not template:
        return None

    # Slugs come from the tracker, which takes them as-is from discovery, so
    # they are not guaranteed URL-safe -- one real company is recorded as
    # "acme widgets inc", spaces and all. Interpolating that raw raises
    # InvalidURL from deep inside http.client. Escaping either produces the
    # right URL or a clean 404, both of which this function handles.
    request = urllib.request.Request(
        template.format(slug=urllib.parse.quote(slug, safe="")),
        headers={"User-Agent": _USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError,
            http.client.HTTPException, UnicodeError):
        # A board that 404s or times out is normal: companies pull boards,
        # rename slugs, and go private between poll cycles.
        #
        # HTTPException is caught alongside them because it is *not* an
        # OSError, so before this it escaped and took down the whole run --
        # one unusable slug out of hundreds killed every posting scored
        # after it.
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def _ashby_job_data(posting: dict, row: dict) -> dict:
    locations = [posting.get("location")] + [
        (secondary or {}).get("location")
        for secondary in posting.get("secondaryLocations") or []
    ]
    return {
        "title": posting.get("title") or row["title"],
        "locations": [loc for loc in locations if loc],
        # Only "remote" is ever asserted: isRemote being false distinguishes
        # nothing between onsite and hybrid, and guessing would be scored.
        "remote_policy": "remote" if posting.get("isRemote") else "unknown",
        "department": " / ".join(
            dict.fromkeys(p for p in (posting.get("department"), posting.get("team")) if p)
        ) or None,
        "description_text": posting.get("descriptionPlain")
        or _strip_html(posting.get("descriptionHtml") or ""),
        "salary_max": None,  # PREFERENCES.min_salary is None; nothing reads this
    }


def _greenhouse_job_data(posting: dict, row: dict) -> dict:
    return {
        "title": posting.get("title") or row["title"],
        "locations": [(posting.get("location") or {}).get("name")]
        if (posting.get("location") or {}).get("name")
        else [],
        "remote_policy": "unknown",
        "department": ", ".join(
            d.get("name", "") for d in posting.get("departments") or []
        ) or None,
        "description_text": _strip_html(posting.get("content") or ""),
        "salary_max": None,
    }


def job_data_for(row: dict, *, refresh: bool = False) -> dict | None:
    """Scorer-shaped job_data for one `seen_jobs` row, or None if the posting
    is no longer on its board (filled, pulled, or reposted under a new id)."""
    board = fetch_board(row["platform"], row["company_slug"], refresh=refresh)
    if not board:
        return None

    wanted = str(row["job_id"])
    posting = next(
        (p for p in board.get("jobs") or [] if str(p.get("id")) == wanted), None
    )
    if not posting:
        return None

    builder = {"ashby": _ashby_job_data, "greenhouse": _greenhouse_job_data}
    job_data = builder[row["platform"]](posting, row)
    job_data["company_name"] = row["company_slug"]
    job_data["url"] = row.get("url")
    return job_data
