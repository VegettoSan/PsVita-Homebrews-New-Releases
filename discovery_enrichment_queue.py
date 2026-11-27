"""Persistent review-only discovery queue. No canonical catalogue writes.

Candidate keys, not offsets, are the durable identity. Store reports only,
never VPK binaries or credentials. UTC timestamps allow retries across runs.
"""
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 1
MAX_RECORDS = 5000
MAX_STATE_BYTES = 32 * 1024 * 1024


def now_utc():
    return datetime.now(timezone.utc)


def fingerprint(entry):
    fields = {k: entry.get(k) for k in ("key", "project", "platform", "author_profile",
              "project_type", "app_draft")} if isinstance(entry, dict) else entry
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def empty_state():
    return {"schema_version": SCHEMA_VERSION, "records": {}}


def load_state(path):
    path = Path(path)
    if not path.exists():
        return empty_state()
    if path.stat().st_size > MAX_STATE_BYTES:
        raise ValueError("Enrichment checkpoint exceeds size limit")
    state = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION
            or not isinstance(state.get("records"), dict) or len(state["records"]) > MAX_RECORDS
            or any(not isinstance(v, dict) for v in state["records"].values())):
        raise ValueError("Invalid enrichment checkpoint; do not silently reset")
    return state


def save_state(path, state):
    path = Path(path)
    raw = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode()
    if len(raw) > MAX_STATE_BYTES:
        raise ValueError("Enrichment checkpoint exceeds size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".enrichment-", delete=False) as handle:
            temp = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def reconcile(state, candidates):
    if len(candidates) > MAX_RECORDS:
        raise ValueError("Candidate inventory exceeds queue capacity")
    records = state["records"]
    # Remove candidates no longer present in the official discovery inventory.
    for key in list(records):
        if key not in candidates:
            del records[key]
    for key, entry in candidates.items():
        mark = fingerprint(entry)
        previous = records.get(key, {})
        if previous.get("fingerprint") != mark:
            records[key] = {"fingerprint": mark, "attempts": 0, "state": "new",
                            "next_at": "", "last_at": ""}
    return state


def due(record, now):
    value = record.get("next_at")
    if not value:
        return True
    return datetime.fromisoformat(value) <= now


def choose(state, candidates, limit, now=None):
    """Rotate by last attempt; prefer nearly complete candidates among equal age."""
    now = now or now_utc()
    rows = []
    for key, record in state["records"].items():
        if not due(record, now):
            continue
        readiness = record.get("result", {}).get("readiness", {})
        present = readiness.get("required_present", 0)
        # Never-attempted first, then oldest attempted: no candidate monopolizes retries.
        rows.append((record.get("last_at", ""), -present, key))
    rows.sort()
    return [(key, candidates[key]) for _, _, key in rows[:limit]]


def checkpoint(state, key, result, now=None):
    now = now or now_utc()
    record = state["records"][key]
    attempts = record.get("attempts", 0)
    status = result.get("state")
    if status == "deferred":
        # Global budget is not a failed candidate attempt; rotate this round.
        record["last_at"] = now.isoformat()
        record["next_at"] = (now + timedelta(minutes=30)).isoformat()
        return
    attempts += 1
    title = result.get("title", {})
    vpk = result.get("vpk", {})
    category = result.get("category", {})
    readiness = result.get("readiness", {})
    if status in {"candidate_error", "internal_error", "partial"} or vpk.get("state") in {"retry", "deferred", "internal_error"}:
        stage = "retry"
        delay = timedelta(hours=min(72, 2 ** min(attempts, 6)))
    elif title.get("state") in {"collision", "retired", "ambiguous"} or category.get("state") == "target_review":
        stage = "review"
        delay = timedelta(days=7)
    elif readiness.get("metadata_complete") and vpk.get("state") == "verified":
        stage = "metadata_complete"
        delay = timedelta(days=3)
    else:
        stage = "pending"
        delay = timedelta(days=1)
    record.update(attempts=attempts, state=stage, last_at=now.isoformat(),
                  next_at=(now + delay).isoformat(), result=result)
    # Even metadata_complete is not approval to publish.


def cached_vpk(record, asset, expected=""):
    """Reuse only if current official API still reports the exact verified digest.

    Without an upstream digest, re-download. Recheck Title ID and catalogue
    collisions on every run. Cache is not final publication authorization.
    """
    if asset.get("state") != "selected" or not asset.get("digest") or type(asset.get("asset_id")) is not int:
        return None
    previous = record.get("result", {}).get("vpk", {})
    tid = previous.get("title_id")
    if (previous.get("state") != "verified" or previous.get("crc_checked") is not True
            or not tid or (expected and tid != expected)
            or previous.get("asset_id") != asset["asset_id"]
            or previous.get("tag") != asset.get("tag")
            or previous.get("url") != asset.get("url")
            or previous.get("bytes_read") != asset.get("size")
            or asset["digest"] != "sha256:" + str(previous.get("sha256", ""))):
        return None
    return {**previous, "cache_hit": True}
