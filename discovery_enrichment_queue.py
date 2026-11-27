"""Persistent review-only discovery queue. No canonical catalogue writes.

Candidate keys, not offsets, are the durable identity. Store reports only,
never VPK binaries or credentials. UTC timestamps allow retries across runs.
"""
import hashlib
import json
import os
import tempfile
import re
from datetime import date, datetime, timedelta, timezone
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


def publication_priority(entry, now):
    """Cheap whole-inventory filter; includes historical and recent releases."""
    if (entry.get("platform") != "github" or entry.get("project_type") != "homebrew"
            or entry.get("lineage_is_fork") is True):
        return False
    draft = entry.get("app_draft") or {}
    try:
        project = entry.get("project", "")
        prefix = "https://github.com/" + project.casefold() + "/releases/download/"
        return (bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", project))
                and date.fromisoformat(draft["version_date"]) <= now.date()
                and type(draft.get("size")) is int and 0 < draft["size"] <= 64*1024*1024
                and isinstance(draft.get("description"), str) and bool(draft["description"].strip())
                and any(l.get("type") == "Download" and str(l.get("url", "")).casefold().startswith(prefix)
                        and str(l.get("url", "")).casefold().endswith(".vpk")
                        for l in draft.get("links", []) if isinstance(l, dict)))
    except (ValueError, TypeError, KeyError):
        return False


def evidence_key(entry, result):
    vpk = result.get("vpk") or {}
    fields = {"fingerprint": fingerprint(entry),
              "package": {k: vpk.get(k) for k in ("tag", "url", "sha256", "title_id", "bytes_read")},
              "category": result.get("category"), "author": result.get("author"),
              "requirements": result.get("requirements")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def source_proof_matches(result, now):
    proof = result.get("publication_preflight") or {}
    asset = proof.get("asset") or {}
    vpk = result.get("vpk") or {}
    try:
        age = now - datetime.fromisoformat(proof["checked_at"])
    except (TypeError, ValueError, KeyError):
        return False
    return (proof.get("state") == "verified" and timedelta(0) <= age <= timedelta(hours=24)
            and asset.get("state") == "selected"
            and asset.get("tag") == vpk.get("tag") and asset.get("url") == vpk.get("url")
            and asset.get("size") == vpk.get("bytes_read")
            and asset.get("asset_id") == vpk.get("asset_id")
            and (not asset.get("digest") or asset["digest"] == "sha256:" + str(vpk.get("sha256", ""))))


def publication_snapshot(state, candidates, apps, registry, taxonomy, feedback=None, now=None):
    """Materialize readiness for every discovery without additional upstream calls.

    This is a scheduling index, not publication authorization. The canonical
    importer independently checks all metadata, live release and package bytes.
    """
    now = now or now_utc()
    feedback = feedback or {}
    ids = {a.get("id") for a in apps} | set(registry.get("apps", {}).get("ids", []))
    tids = {str(a.get("title_id", "")).upper() for a in apps} | {str(t).upper() for t in registry.get("apps", {}).get("title_ids", [])}
    source_ids = {a.get("source_id") for a in apps if a.get("source_name") == "Orbit"}
    sources = {str(a.get("source_url", "")).rstrip("/").casefold() for a in apps}
    allowed = {c.get("id"): {s.get("id") for s in c.get("subcategories", [])} for c in taxonomy}
    records = {}
    for key, entry in candidates.items():
        record = state["records"].get(key, {})
        draft = entry.get("app_draft") or {}
        mark = fingerprint(entry)
        result = record.get("result", {}) if record.get("fingerprint") == mark else {}
        vpk = result.get("vpk") or {}
        title = result.get("title") or {}
        cat = result.get("category") or {}
        author = result.get("author") or {}
        req = result.get("requirements") or {}
        project = str(entry.get("project", ""))
        tid = vpk.get("title_id")
        app_id = draft.get("id")
        item = {"fingerprint": mark, "sha256": vpk.get("sha256", ""), "state": "DISCOVERED"}
        if (key in source_ids or app_id in ids or
                (project and ("https://github.com/" + project).casefold() in sources) or
                (tid and tid in tids)):
            item["state"] = "DUPLICATE"
        elif (result.get("publication_preflight") or {}).get("state") in ("WAITING_RELEASE", "NEEDS_REVIEW", "RETRY_LATER"):
            proof = result["publication_preflight"]
            item.update(state=proof["state"], reason=proof.get("reason", "source_review"))
        elif record.get("state") == "review":
            item["state"] = "NEEDS_REVIEW"
        elif (source_proof_matches(result, now) and result.get("state") == "inspected" and title.get("state") == "high"
                and isinstance(tid, str) and re.fullmatch(r"[A-Z0-9]{9}", tid)
                and title.get("suggestion") == tid
                and vpk.get("state") == "verified" and vpk.get("crc_checked") is True
                and re.fullmatch(r"[a-f0-9]{64}", str(vpk.get("sha256", "")))
                and type(vpk.get("bytes_read")) is int and 0 < vpk["bytes_read"] <= 64*1024*1024
                and (vpk.get("artwork") or {}).get("icon")
                and cat.get("state") == "suggested" and cat.get("category_id") in allowed
                and bool(cat.get("subcategory_ids") or ("other" in allowed.get(cat.get("category_id"), set())))
                and set(cat.get("subcategory_ids") or []) <= allowed.get(cat.get("category_id"), set())
                and author.get("state") in ("existing", "draft") and req.get("state") != "review"
                and publication_priority(entry, now)
                and isinstance(app_id, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,70}", app_id)
                and any(l.get("type") == "Download" and l.get("url") == vpk.get("url")
                        for l in draft.get("links", []) if isinstance(l, dict))):
            item["state"] = "READY_TO_PUBLISH"
            item["title_id"] = tid
            item["app_id"] = app_id
        elif publication_priority(entry, now):
            item["state"] = "PROMISING"
        elif record.get("state") == "retry":
            item["state"] = "RETRY_LATER"
        elif result:
            item["state"] = "PREPARING"
        prior = feedback.get(key, {})
        if item["state"] not in ("DUPLICATE", "NEEDS_REVIEW") and isinstance(prior, dict) and prior.get("evidence") == evidence_key(entry, result):
            try:
                if datetime.fromisoformat(prior["retry_at"]) > now:
                    item.update(state="RETRY_LATER", reason=prior.get("reason", "retry"), retry_at=prior["retry_at"])
            except (ValueError, TypeError, KeyError):
                pass
        records[key] = item
    from collections import Counter
    ready = [item for item in records.values() if item["state"] == "READY_TO_PUBLISH"]
    counts_id = Counter(item["app_id"] for item in ready)
    counts_tid = Counter(item["title_id"] for item in ready)
    for item in ready:
        if counts_id[item["app_id"]] > 1 or counts_tid[item["title_id"]] > 1:
            item["state"] = "NEEDS_REVIEW"
    counts = Counter(item["state"] for item in records.values())
    state["publication_queue"] = {"schema_version": 2, "records": records, "counts": dict(counts)}
    return state["publication_queue"]


def choose(state, candidates, limit, now=None, prioritize_publication=False):
    """Rotate by last attempt; prefer nearly complete candidates among equal age."""
    now = now or now_utc()
    rows = []
    index = (state.get("publication_queue") or {}).get("records", {})
    for key, record in state["records"].items():
        hint = index.get(key, {})
        result = record.get("result") or {}
        # Revalidate old cached packages once rather than waiting for their old checkpoint TTL.
        migration = (prioritize_publication and hint.get("state") in ("PROMISING", "RETRY_LATER")
                     and (hint.get("state") != "RETRY_LATER" or hint.get("reason") == "release_changed")
                     and (result.get("vpk") or {}).get("state") == "verified"
                     and not source_proof_matches(result, now))
        if prioritize_publication and (hint.get("state") == "DUPLICATE" or
                (hint.get("state") == "RETRY_LATER" and hint.get("retry_at") and not migration)):
            continue
        if not due(record, now) and not migration:
            continue
        readiness = record.get("result", {}).get("readiness", {})
        present = readiness.get("required_present", 0)
        # Never-attempted first, then oldest attempted: no candidate monopolizes retries.
        rows.append(("" if migration else record.get("last_at", ""), -present - (100 if migration else 0), key))
    rows.sort()
    if prioritize_publication:
        # Reserve 80% for promising candidates; keep a fair exploration lane.
        # Ready candidates already have evidence and need not monopolize it.
        index = (state.get("publication_queue") or {}).get("records", {})
        priority = [row for row in rows if publication_priority(candidates[row[2]], now)
                    and (index.get(row[2], {}).get("state", "PROMISING") == "PROMISING" or row[1] < -100)][:max(1, limit*4//5)]
        keys = {row[2] for row in priority}
        rows = priority + [row for row in rows if row[2] not in keys]
    return [(key, candidates[key]) for _, _, key in rows[:limit]]


def checkpoint(state, key, result, now=None, quota=None):
    now = now or now_utc()
    record = state["records"][key]
    attempts = record.get("attempts", 0)
    status = result.get("state")
    if status == "deferred":
        # Global budget is not a failed candidate attempt; rotate this round.
        record["last_at"] = now.isoformat()
        record["next_at"] = (quota_resume(quota, now) or now + timedelta(minutes=30)).isoformat()
        return
    attempts += 1
    title = result.get("title", {})
    vpk = result.get("vpk", {})
    category = result.get("category", {})
    readiness = result.get("readiness", {})
    if status == "source_wait":
        proof = result.get("publication_preflight") or {}
        stage = "review" if proof.get("state") == "NEEDS_REVIEW" else "retry"
        delay = timedelta(days=7) if proof.get("reason") == "upstream_missing" else timedelta(days=3) if stage == "review" else timedelta(hours=12)
    elif status in {"candidate_error", "internal_error", "partial"} or vpk.get("state") in {"retry", "deferred", "internal_error"}:
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

def quota_resume(quota, now):
    if not quota or not quota.get("rate_limited"):
        return None
    try:
        reset = datetime.fromisoformat(quota.get("reset_at") or "")
        if reset.tzinfo is not None and now < reset <= now + timedelta(hours=2):
            return reset + timedelta(seconds=60)
    except (TypeError, ValueError):
        pass
    return now + timedelta(minutes=30)


def paused(state, now=None):
    now = now or now_utc()
    value = state.get("resume_at")
    return bool(value and datetime.fromisoformat(value) > now)


def apply_quota_pause(state, quota, now=None):
    resume = quota_resume(quota, now or now_utc())
    if resume is None:
        state.pop("resume_at", None)
    else:
        state["resume_at"] = resume.isoformat()


def summary(state, selected, results=()):
    counts = {}
    for record in state["records"].values():
        stage = record.get("state", "new")
        counts[stage] = counts.get(stage, 0) + 1
    return {"inventory": len(state["records"]), "states": counts,
            "selected_keys": selected,
            "processed_keys": [r["key"] for r in results if r.get("state") != "deferred"],
            "resume_at": state.get("resume_at"), "cached_profiles": len(state.get("profiles", {})),
            "publication_states": (state.get("publication_queue") or {}).get("counts", {})}


def profile_request(state, owner, request, now=None):
    """Cache only verified public GitHub user fields; revalidate official author
    matches and retired IDs through author_proposal on every use.
    """
    now = now or now_utc()
    profiles = state.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("Invalid profile cache")
    for key, value in list(profiles.items()):
        try:
            if datetime.fromisoformat(value["expires_at"]) <= now:
                del profiles[key]
        except (KeyError, TypeError, ValueError, AttributeError):
            del profiles[key]

    def lookup(url, *args, **kwargs):
        match = re.fullmatch(r"https://api.github.com/users/([a-zA-Z0-9-]{1,39})", url)
        if not match or type(owner.get("id")) is not int:
            return request(url, *args, **kwargs)
        handle = match.group(1).casefold()
        key = str(owner["id"]) + ":" + handle
        cached = profiles.get(key)
        if cached is not None:
            return dict(cached["data"])
        data = request(url, *args, **kwargs)
        if (isinstance(data, dict) and data.get("type") == "User"
                and data.get("id") == owner["id"]
                and str(data.get("login", "")).casefold() == handle
                and len(profiles) < MAX_RECORDS):
            fields = {k: data.get(k) for k in ("id", "type", "login", "html_url", "name", "bio", "avatar_url")}
            profiles[key] = {"expires_at": (now + timedelta(days=7)).isoformat(), "data": fields}
        return data
    return lookup
