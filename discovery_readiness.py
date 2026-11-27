"""Read-only readiness report; not a replacement for PSVitaAlive validation."""
from datetime import date
import re
from urllib.parse import urlsplit

REQUIRED = ("id", "title_id", "name", "description", "long_description",
            "author_ids", "category_id", "subcategory_ids", "version",
            "version_date", "requirements", "size", "status", "links", "icon")


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def web_url(value):
    if not nonempty(value):
        return False
    try:
        u = urlsplit(value)
        return u.scheme == "https" and bool(u.hostname) and not u.username and not u.password and not any(c.isspace() for c in value)
    except ValueError:
        return False


def readiness(entry, result, categories, authors, official_apps, registry):
    """Conservative priority list: required fields first, optional media last.

    Does not authorize publishing, assign status, verify media bytes, or create
    author profiles. The canonical validator remains authoritative.
    """
    app = entry.get("app_draft") or {}
    taxonomy = {c["id"]: {s["id"] for s in c.get("subcategories", [])}
                for c in categories}
    existing_ids = {a.get("id") for a in official_apps}
    existing_titles = {str(a.get("title_id", "")).upper() for a in official_apps}
    retired = registry.get("apps", {})
    retired_ids = set(retired.get("ids", []))
    retired_titles = {str(x).upper() for x in retired.get("title_ids", [])}
    active_authors = {a.get("id") for a in authors}
    retired_authors = set(registry.get("authors", {}).get("ids", []))
    fields = {}
    defaults = {}
    def check(key, ok, reason):
        fields[key] = {"state": "present" if ok else "pending", "reason": "" if ok else reason}

    aid = app.get("id")
    check("id", nonempty(aid) and bool(re.fullmatch(r"[a-z0-9_-]+", aid))
          and aid not in existing_ids and aid not in retired_ids, "Missing, invalid, existing or retired ID")
    title = result.get("title", {})
    tid = title.get("suggestion")
    check("title_id", title.get("state") == "high" and nonempty(tid)
          and bool(re.fullmatch(r"[A-Z0-9]{9}", tid)) and tid not in existing_titles
          and tid not in retired_titles, "Need unambiguous high-confidence Title ID; package verification still pending")
    for key in ("name", "description"):
        check(key, nonempty(app.get(key)), "Need documented " + key)
    if nonempty(app.get("long_description")):
        check("long_description", True, "")
    elif nonempty(app.get("description")):
        defaults["long_description"] = app["description"]
        check("long_description", True, "")
    else:
        check("long_description", False, "Need a real description to reuse")
    ids = app.get("author_ids")
    check("author_ids", isinstance(ids, list) and bool(ids) and all(
        nonempty(x) and x in active_authors and x not in retired_authors for x in ids)
        and len(set(ids)) == len(ids), "Need verified existing author profiles or approved new author draft")
    category = result.get("category", {})
    cid = category.get("category_id")
    valid_category = category.get("state") == "suggested" and cid in taxonomy
    check("category_id", valid_category, "Need official category with sufficient evidence")
    subs = category.get("subcategory_ids")
    valid_subs = valid_category and isinstance(subs, list) and bool(subs) and all(
        nonempty(x) and x in taxonomy[cid] for x in subs) and len(set(subs)) == len(subs)
    if valid_category and subs == [] and "other" in taxonomy[cid]:
        defaults["subcategory_ids"] = ["other"]
        valid_subs = True
    check("subcategory_ids", valid_subs, "Canonical validator requires at least one valid subcategory")
    check("version", nonempty(app.get("version")), "Need documented version")
    vd = app.get("version_date")
    try:
        valid_date = nonempty(vd) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", vd)) and date.fromisoformat(vd) <= date.today()
    except ValueError:
        valid_date = False
    check("version_date", valid_date, "Need actual release date YYYY-MM-DD")
    req = result.get("requirements", {})
    value = req.get("value")
    if not nonempty(value):
        value = "- no data"
    defaults["requirements"] = value
    check("requirements", req.get("state") != "review", "Contradictory requirements require review")
    size = app.get("size")
    check("size", type(size) is int and size > 0, "Need positive size in bytes from source")
    check("status", app.get("status") in {"Verified", "Legacy", "Archive"}, "Need status under approved publication policy; never infer Verified")
    links = app.get("links")
    valid_links = isinstance(links, list) and bool(links) and all(
        isinstance(x, dict) and all(nonempty(x.get(k)) for k in ("type", "name"))
        and web_url(x.get("url")) for x in links)
    if valid_links:
        download = [x for x in links if x["type"] == "Download"]
        source = [x for x in links if x["type"] in {"Repository", "Official Website"}]
        valid_links = bool(download and source) and sum(x.get("recommended") is True for x in links) <= 1
    check("links", valid_links, "Need valid official source and download links")
    check("icon", web_url(app.get("icon")), "Need application icon URL; image verification still pending")
    missing = [k for k in REQUIRED if fields[k]["state"] == "pending"]
    return {"required_total": len(REQUIRED), "required_present": len(REQUIRED) - len(missing),
            "missing_required": missing, "fields": fields, "proposed_defaults": defaults,
            "metadata_complete": not missing, "publication_allowed": False,
            "screenshots_optional": True,
            "note": "Preflight only; canonical validation, identity, media, authorization and status approval still required"}
