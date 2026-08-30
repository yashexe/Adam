"""
Job descriptions, fetched from the public board APIs.

`seen_jobs` stores only what the tracker derived from a posting
(funding_hint, comp_summary) — never the description itself. Without the
description the judge has nothing to read (and the judge's reading is the
score), and the years/citizenship eligibility rules have nothing to
extract from. So the text has to be re-fetched.

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
LEVER_API = "https://api.lever.co/v0/postings/{slug}?mode=json"
# Workable is per-posting, not per-board: the tracker harvests from the
# cross-customer feed, and the detail endpoint accepts the same job UUID
# the tracker stores. Deliberately NOT apply.workable.com (the widget API)
# — it rate-limits into a ~15-minute IP-wide lockout under bulk use; see
# docs/tracker-upstream-2026-08-30.md.
WORKABLE_JOB_API = "https://jobs.workable.com/api/v1/jobs/{job_id}"

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

    template = {
        "ashby": ASHBY_API,
        "greenhouse": GREENHOUSE_API,
        "lever": LEVER_API,
    }.get(platform)
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

    # Lever's v0 postings API returns a bare array rather than an object;
    # normalize before caching so every cached board reads the same way.
    if isinstance(data, list):
        data = {"jobs": data}

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
        "salary_max": None,  # nothing reads this since the composite died
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


def _lever_job_data(posting: dict, row: dict) -> dict:
    # Lever splits the posting body across descriptionPlain, per-section
    # `lists` (requirements/responsibilities live here), and an
    # additionalPlain footer -- description alone is just the intro, and
    # scoring on it would blind the extractor to the requirements.
    parts = [posting.get("descriptionPlain") or _strip_html(posting.get("description") or "")]
    for section in posting.get("lists") or []:
        parts.append(section.get("text") or "")
        parts.append(_strip_html(section.get("content") or ""))
    parts.append(posting.get("additionalPlain") or "")
    categories = posting.get("categories") or {}
    workplace = (posting.get("workplaceType") or "").lower()
    return {
        "title": posting.get("text") or row["title"],
        "locations": [loc for loc in [categories.get("location")] if loc],
        "remote_policy": workplace if workplace in ("remote", "hybrid", "onsite") else "unknown",
        "department": categories.get("team") or categories.get("department"),
        "description_text": " ".join(p for p in parts if p).strip(),
        "salary_max": None,
    }


def _workable_job_data(posting: dict, row: dict) -> dict:
    location = posting.get("location") or {}
    location_name = ", ".join(
        p for p in (location.get("city"), location.get("subregion"),
                    location.get("countryName")) if p
    )
    workplace = (posting.get("workplace") or "").lower()
    text = " ".join(
        _strip_html(posting.get(f) or "")
        for f in ("description", "requirementsSection", "benefitsSection")
    ).strip()
    return {
        "title": posting.get("title") or row["title"],
        "locations": [location_name] if location_name else [],
        "remote_policy": workplace if workplace in ("remote", "hybrid") else "unknown",
        "department": (posting.get("department") or None),
        "description_text": text,
        "salary_max": None,
    }


def _fetch_workable_job(job_id: str, *, refresh: bool = False) -> dict | None:
    """One posting from Workable's cross-customer feed, cached per posting
    (there is no fetchable per-company board on this platform)."""
    path = _cache_path("workable", job_id)
    if not refresh and path.exists():
        if time.time() - path.stat().st_mtime < CACHE_TTL_SECONDS:
            try:
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                pass
    request = urllib.request.Request(
        WORKABLE_JOB_API.format(job_id=urllib.parse.quote(str(job_id), safe="")),
        headers={"User-Agent": _USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError,
            http.client.HTTPException, UnicodeError):
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def job_data_for(row: dict, *, refresh: bool = False) -> dict | None:
    """Scorer-shaped job_data for one `seen_jobs` row, or None if the posting
    is no longer on its board (filled, pulled, or reposted under a new id)."""
    if row["platform"] == "workable":
        posting = _fetch_workable_job(str(row["job_id"]), refresh=refresh)
        if not posting:
            return None
        job_data = _workable_job_data(posting, row)
        job_data["company_name"] = row["company_slug"]
        job_data["url"] = row.get("url")
        return job_data

    board = fetch_board(row["platform"], row["company_slug"], refresh=refresh)
    if not board:
        return None

    wanted = str(row["job_id"])
    posting = next(
        (p for p in board.get("jobs") or [] if str(p.get("id")) == wanted), None
    )
    if not posting:
        return None

    builder = {
        "ashby": _ashby_job_data,
        "greenhouse": _greenhouse_job_data,
        "lever": _lever_job_data,
    }
    job_data = builder[row["platform"]](posting, row)
    job_data["company_name"] = row["company_slug"]
    job_data["url"] = row.get("url")
    return job_data
