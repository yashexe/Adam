#!/usr/bin/env python3
"""
The unattended wrapper launchd runs every five minutes.

    python3 bin/tick.py            # what launchd runs
    python3 bin/tick.py --dry-run  # tick, report the verdict, launch nothing

Runs `outreach_run.py tick`; on `fire`, launches one headless Claude
session with the outreach skill in unattended mode, under a lock so two
never overlap and a wall-clock timeout so a wedged run cannot hold the
lock all day, then reports back with `run-done` so the queue can retry or
drop. Everything the model may touch is enumerated in ALLOWED_TOOLS;
anything else is denied without asking. There is no send path anywhere
in this repository and this file adds none.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable
CLAUDE = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
RUN_TIMEOUT_SECONDS = 25 * 60
LOCK_STALE_SECONDS = 45 * 60
PROMPT_PATH = REPO / "bin" / "unattended_prompt.md"
ALLOWED_TOOLS = [
    "Read", "Write", "Agent", "Skill", "WebSearch", "WebFetch",
    "Bash(python3 outreach_run.py:*)", "Bash(date:*)",
]


def state_dir() -> Path:
    d = Path(os.getenv("ADAM_UNATTENDED_DIR") or REPO / ".cache" / "unattended")
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(REPO / "outreach_run.py"), *args],
                          cwd=REPO, capture_output=True, text=True)


def acquire_lock(lock: Path) -> bool:
    try:
        lock.mkdir()
        (lock / "pid").write_text(str(os.getpid()))
        return True
    except FileExistsError:
        age = time.time() - lock.stat().st_mtime
        if age > LOCK_STALE_SECONDS:
            shutil.rmtree(lock, ignore_errors=True)
            return acquire_lock(lock)
        return False


def notify(title: str, text: str) -> None:
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification "{text[:180]}" with title "{title}"'],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def main() -> int:
    dry = "--dry-run" in sys.argv
    tick = run_cli("tick")
    verdict = (tick.stdout.strip().splitlines() or ["idle"])[0]
    reason = tick.stderr.strip()
    if tick.returncode != 0:
        print(f"tick failed: {tick.stderr.strip()[:300]}", file=sys.stderr)
        return 1
    print(f"{verdict}: {reason}")
    if verdict != "fire" or dry:
        return 0

    lock = state_dir() / "run.lock"
    if not acquire_lock(lock):
        print("another run holds the lock; leaving the queue in flight", file=sys.stderr)
        return 0
    runs = state_dir() / "runs"
    runs.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = runs / f"{stamp}.log"
    summary_path = runs / f"{stamp}.summary.md"
    prompt = PROMPT_PATH.read_text().replace("{SUMMARY_PATH}", str(summary_path))
    status = "fail"
    try:
        with log_path.open("w") as log:
            proc = subprocess.run(
                [CLAUDE, "-p", prompt, "--permission-mode", "dontAsk",
                 "--allowedTools", *ALLOWED_TOOLS, "--output-format", "text"],
                cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
                timeout=RUN_TIMEOUT_SECONDS, env={**os.environ, "ADAM_UNATTENDED": "1"},
            )
        status = "ok" if proc.returncode == 0 else "fail"
    except subprocess.TimeoutExpired:
        status = "timeout"
    finally:
        done = run_cli("run-done", "--status", status, "--summary-file", str(summary_path))
        shutil.rmtree(lock, ignore_errors=True)
    summary = summary_path.read_text() if summary_path.exists() else ""
    drafted = summary.count("DRAFTED:")
    parked = summary.count("SLATE:")
    if status == "ok" and (drafted or parked):
        notify("Adam", f"{drafted} draft(s) in Gmail, {parked} slate(s) awaiting your pick")
    elif status != "ok":
        notify("Adam", f"unattended run {status}; see {log_path.name}")
    print(f"run {status}: {drafted} drafted, {parked} parked; log {log_path.name}")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
