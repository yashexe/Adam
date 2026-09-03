"""
The unattended trigger: a deterministic tick that decides *when* an LLM run
is worth launching, and the bookkeeping that keeps such runs bounded.

Nothing here calls a model. The tick runs every five minutes from launchd
(bin/tick.py, com.yash.adam-tick.plist), matches the Pi's own poll
interval, and costs one read-only SSH query. It queues new eligible
postings behind a watermark and fires when the oldest has waited
FIRE_AGE_MINUTES or FIRE_COUNT have piled up -- the batching that keeps
the judge's fixed per-call cost (profile + anchors, ~8k tokens) from being
paid once per posting. A human pick waiting in the slates table also
fires, since drafting it costs no research.

Budgets are counted here, not trusted to the prompt: the run asks for its
budget, reports each company it starts, and the wrapper reports how the
run ended. A failed run puts its postings back for one retry; a second
failure drops them, and the day window still covers them for the next
interactive run. State lives in .cache/unattended/state.json; a different
directory can be pointed at with ADAM_UNATTENDED_DIR for tests.

Decided 2026-09-03 (docs/decisions.md): draft to rank one on a clean
resolve for scores >= 70, park the slate otherwise, three companies per
run, eight per day.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from qualify.candidates import fetch_candidates
from qualify.eligibility import check_title

from outreach import store

FIRE_AGE_MINUTES = int(os.getenv("ADAM_FIRE_AGE_MINUTES", 15))
FIRE_COUNT = int(os.getenv("ADAM_FIRE_COUNT", 5))
PER_RUN = int(os.getenv("ADAM_PER_RUN", 3))
PER_DAY = int(os.getenv("ADAM_PER_DAY", 8))
RETRY_MAX = 2
STALE_RUN_MINUTES = 45  # an inflight run older than this is assumed dead


def state_dir() -> Path:
    override = os.getenv("ADAM_UNATTENDED_DIR")
    base = Path(override) if override else Path(__file__).resolve().parent.parent / ".cache" / "unattended"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _stamp(dt: datetime) -> str:
    """The tracker's first_seen_at format: 'YYYY-MM-DD HH:MM:SS', UTC."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def load_state() -> dict:
    path = state_dir() / "state.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    return {"watermark": None, "queue": [], "inflight": None,
            "daily": {"date": None, "started": []}, "last_run": None}


def save_state(state: dict) -> None:
    (state_dir() / "state.json").write_text(json.dumps(state, indent=2))


def log(line: str) -> None:
    with (state_dir() / "tick.log").open("a") as fh:
        fh.write(f"{_stamp(_now())} {line}\n")


def _roll_day(state: dict) -> None:
    today = _now().strftime("%Y-%m-%d")
    if state["daily"].get("date") != today:
        state["daily"] = {"date": today, "started": []}


def budget(state: dict | None = None) -> dict:
    state = state or load_state()
    _roll_day(state)
    used = len(state["daily"]["started"])
    remaining_today = max(PER_DAY - used, 0)
    return {
        "per_run": min(PER_RUN, remaining_today),
        "remaining_today": remaining_today,
        "started_today": list(state["daily"]["started"]),
        "approved_slates": [r["company_slug"] for r in store.slates(store.SLATE_APPROVED)],
    }


def tick(*, since: str | None = None) -> tuple[str, str]:
    """One deterministic check. Returns ('fire' | 'idle', reason)."""
    state = load_state()
    _roll_day(state)
    now = _now()

    if since:
        state["watermark"] = since
    if not state["watermark"]:
        # First ever tick starts from now: nothing is back-filled unless
        # asked with --since, so installing the job cannot trigger a burst.
        state["watermark"] = _stamp(now)

    # A run that never reported back is either still going or dead.
    inflight = state.get("inflight")
    if inflight:
        fired = datetime.strptime(inflight["fired_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if now - fired < timedelta(minutes=STALE_RUN_MINUTES):
            save_state(state)
            return "idle", f"run in flight since {inflight['fired_at']}"
        log(f"inflight run from {inflight['fired_at']} never reported; treating as failed")
        run_done(state, "stale")
        state = load_state()

    # New postings since the watermark, filtered by what costs nothing.
    rows = fetch_candidates(days=2)
    queued = {f"{q['platform']}:{q['job_id']}" for q in state["queue"]}
    ignored = store.ignored()
    added = 0
    newest = state["watermark"]
    for row in sorted(rows, key=lambda r: r.get("first_seen_at") or ""):
        seen = row.get("first_seen_at") or ""
        if seen <= state["watermark"]:
            continue
        newest = max(newest, seen)
        key = f"{row['platform']}:{row['job_id']}"
        if key in queued:
            continue
        slug = row["company_slug"]
        if slug in ignored or not check_title(row.get("title") or "")[0]:
            continue
        claim, _ = store.claim_state(slug)
        if claim is not None and claim != store.DISCARDED:
            continue
        slate = store.slate_row(slug)
        if slate is not None and slate["status"] in (store.SLATE_AWAITING, store.SLATE_APPROVED):
            continue
        state["queue"].append({
            "platform": row["platform"], "job_id": str(row["job_id"]), "company_slug": slug,
            "title": row.get("title"), "url": row.get("url"), "first_seen_at": seen,
            "queued_at": _stamp(now),
        })
        queued.add(key)
        added += 1
    state["watermark"] = newest

    b = budget(state)
    queue = state["queue"]
    reason = f"{added} new, {len(queue)} queued, {b['remaining_today']} left today"
    if b["remaining_today"] <= 0 and not b["approved_slates"]:
        save_state(state); log(f"idle: daily budget spent ({reason})")
        return "idle", f"daily budget spent ({reason})"
    oldest_age = None
    if queue:
        oldest = min(datetime.strptime(q["queued_at"], "%Y-%m-%d %H:%M:%S") for q in queue)
        oldest_age = now.replace(tzinfo=None) - oldest
    ready = bool(queue) and (len(queue) >= FIRE_COUNT or oldest_age >= timedelta(minutes=FIRE_AGE_MINUTES))
    if ready or b["approved_slates"]:
        attempt = 1 + max((q.get("attempt", 0) for q in queue), default=0)
        state["inflight"] = {"fired_at": _stamp(now), "postings": queue, "attempt": attempt,
                             "approved": b["approved_slates"]}
        state["queue"] = []
        save_state(state)
        why = ("approved pick waiting" if not ready else
               f"{len(state['inflight']['postings'])} posting(s), oldest {int(oldest_age.total_seconds()//60)} min")
        log(f"fire: {why} ({reason})")
        return "fire", why
    save_state(state)
    wait = ""
    if oldest_age is not None:
        wait = f", oldest waited {int(oldest_age.total_seconds()//60)} of {FIRE_AGE_MINUTES} min"
    log(f"idle: {reason}{wait}")
    return "idle", reason + wait


def start_company(slug: str) -> dict:
    """The run is about to spend a contact search on this company."""
    state = load_state()
    _roll_day(state)
    if slug not in state["daily"]["started"]:
        state["daily"]["started"].append(slug)
    save_state(state)
    return budget(state)


def run_done(state: dict | None, status: str, summary: str | None = None) -> dict:
    """The wrapper reports how the run ended. On failure the postings go
    back in the queue once; a second failure drops them."""
    state = state or load_state()
    inflight = state.get("inflight") or {}
    postings = inflight.get("postings", [])
    attempt = inflight.get("attempt", 1)
    requeued = 0
    if status != "ok" and postings and attempt < RETRY_MAX:
        for p in postings:
            p["attempt"] = attempt
        state["queue"] = postings + state["queue"]
        requeued = len(postings)
    state["last_run"] = {"fired_at": inflight.get("fired_at"), "finished_at": _stamp(_now()),
                         "status": status, "postings": len(postings), "requeued": requeued,
                         "summary": (summary or "")[:2000]}
    state["inflight"] = None
    save_state(state)
    log(f"run {status}: {len(postings)} posting(s), {requeued} requeued")
    return state["last_run"]
