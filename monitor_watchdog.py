#!/usr/bin/env python3
"""Recover missed scheduled Orbit scans using GitHub Actions only.

The normal three-hour schedule remains in monitor.yml. This hourly watchdog
runs at an offset minute, checks GitHub's *actual* schedule/manual scan history,
and dispatches a fresh full scan only when one is overdue. Catalog refresh
(repository_dispatch) is intentionally excluded: it is not a full scan.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

WORKFLOW = "monitor.yml"
SCAN_EVENTS = ("schedule", "workflow_dispatch")
FULL_SCAN_INTERVAL = timedelta(hours=3)
FAILED_SCAN_RETRY = timedelta(hours=1)
ACTIVE_STATUSES = frozenset({"queued", "in_progress", "pending", "requested", "waiting"})


def github_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(timezone.utc) if result.tzinfo else None


def recovery_decision(runs: list[dict], now: datetime) -> tuple[bool, str]:
    """Fail closed if a run cannot be reliably dated; never duplicate active scans."""
    if not runs:
        return True, "No previous full scan was found"

    dated = [(run, github_time(run.get("created_at"))) for run in runs]
    if any(created is None for _, created in dated):
        return False, "Invalid GitHub run timestamp; skipping unsafe dispatch"

    latest, created = max(dated, key=lambda item: item[1])
    status = latest.get("status")
    if status in ACTIVE_STATUSES:
        return False, f"Full scan {latest.get('id')} is still {status}"

    if status != "completed":
        return False, f"Unknown full scan status: {status!r}"

    completed = github_time(latest.get("updated_at")) or created
    if completed > now:
        return False, "Scan timestamp is in the future; skipping unsafe dispatch"

    age = now - completed
    threshold = (FULL_SCAN_INTERVAL if latest.get("conclusion") == "success"
                 else FAILED_SCAN_RETRY)
    if age < threshold:
        return False, (f"Last full scan {latest.get('id')} is only "
                       f"{int(age.total_seconds() // 60)} minutes old")

    return True, (f"Last full scan {latest.get('id')} is overdue "
                  f"({int(age.total_seconds() // 60)} minutes)")


def github_api(repo: str, token: str, path: str, *, method: str = "GET",
               payload: dict | None = None) -> object:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"https://api.github.com/repos/{repo}/{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PSVitaAlive-Orbit-Schedule-Watchdog",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    with urlopen(request, timeout=20) as response:
        if response.status == 204:
            return None
        return json.load(response)


def recent_full_scans(repo: str, token: str) -> list[dict]:
    runs = []
    for event in SCAN_EVENTS:
        params = urlencode({"event": event, "per_page": 10})
        result = github_api(repo, token,
                            f"actions/workflows/{WORKFLOW}/runs?{params}")
        if not isinstance(result, dict) or not isinstance(result.get("workflow_runs"), list):
            raise ValueError(f"Unexpected GitHub API response for {event}")
        runs.extend(result["workflow_runs"])
    return runs


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not token:
        print("Missing GitHub repository or authentication; refusing dispatch", file=sys.stderr)
        return 1

    # Any API error stops the watchdog. Do not dispatch when history is unknown.
    try:
        runs = recent_full_scans(repo, token)
        dispatch, reason = recovery_decision(runs, datetime.now(timezone.utc))
        print(reason, flush=True)
        if dispatch:
            github_api(repo, token, f"actions/workflows/{WORKFLOW}/dispatches",
                       method="POST", payload={"ref": "main"})
            print("Recovery workflow_dispatch sent to Orbit monitor.yml", flush=True)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Unable to safely check/dispatch monitor: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
