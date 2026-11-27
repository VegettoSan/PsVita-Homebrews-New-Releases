"""Port of web/app-generator/author-profile-autofill.js, with automation safeguards.

Public profile drafts only: a repository owner is not proof of original authorship.
"""
import re
import unicodedata
from urllib.parse import urlsplit, quote

DEFAULT_AVATAR = "https://raw.githubusercontent.com/VegettoSan/PSVitaAlive/main/authors/icon/autoricon.png"


def slugify(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


def account_key(url, profile_only=False):
    try:
        u = urlsplit(str(url or ""))
        host = (u.hostname or "").removeprefix("www.")
        parts = [p for p in u.path.split("/") if p]
        if (u.scheme != "https" or host not in {"github.com", "gitlab.com"}
                or u.username or u.password or u.port or not parts
                or (profile_only and len(parts) != 1)):
            return ""
        name = parts[0]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", name):
            return ""
        if host == "github.com" and not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", name):
            return ""
        return host + "/" + name.casefold()
    except ValueError:
        return ""


def author_proposal(entry, owner, authors, registry, request):
    """Reuse exact account matches, otherwise fetch public profile like the web."""
    profile = entry.get("author_profile")
    key = account_key(profile, profile_only=True)
    if not key or key != account_key(owner.get("url"), profile_only=True):
        return {"state": "review", "reason": "Owner/profile identity unconfirmed"}
    retired = set(registry.get("authors", {}).get("ids", []))
    matches = [a for a in authors if any(account_key(l.get("url")) == key
               for l in a.get("links", []) if isinstance(l, dict))]
    if len(matches) > 1 or any(a.get("id") in retired for a in matches):
        return {"state": "review", "reason": "Conflicting or retired account"}
    if len(matches) == 1:
        return {"state": "existing", "author_ids": [matches[0]["id"]]}
    if owner.get("fork") or owner.get("group"):
        return {"state": "review", "reason": "Fork/group ownership is not original author attribution"}
    host, handle = key.split("/", 1)
    if host == "github.com":
        data = request("https://api.github.com/users/" + quote(handle, safe=""))
        if not isinstance(data, dict) or data.get("type") != "User" or account_key(data.get("html_url"), True) != key or data.get("id") != owner.get("id"):
            return {"state": "review", "reason": "GitHub public user identity not confirmed"}
        username, name, bio, avatar = data.get("login"), data.get("name"), data.get("bio"), data.get("avatar_url")
        provider = "GitHub"
    else:
        users = request("https://gitlab.com/api/v4/users", {"username": handle}, gitlab=True)
        matches = [u for u in users or [] if isinstance(u, dict) and
                   account_key(u.get("web_url"), True) == key and str(u.get("username", "")).casefold() == handle]
        if len(matches) != 1:
            return {"state": "review", "reason": "GitLab public user identity not confirmed"}
        data = matches[0]
        details = request("https://gitlab.com/api/v4/users/" + str(int(data["id"])), gitlab=True)
        if isinstance(details, dict) and details.get("id") == data["id"]:
            data = {**data, **details}
        username, name, bio, avatar = data.get("username"), data.get("name"), data.get("bio"), data.get("avatar_url")
        provider = "GitLab"
    aid = slugify(username)
    if not aid or aid in retired or any(a.get("id") == aid or slugify(a.get("name")) == aid for a in authors):
        return {"state": "review", "reason": "Author slug/name collision or retired identifier"}
    try:
        u = urlsplit(str(avatar or ""))
        valid_avatar = u.scheme == "https" and bool(u.hostname) and not u.username and not u.password
    except ValueError:
        valid_avatar = False
    draft = {"id": aid, "name": str(name or username).strip()[:120] or username,
             "bio": str(bio or "").strip() or "Developer",
             "avatar": avatar if valid_avatar else DEFAULT_AVATAR,
             "links": [{"type": "Repository", "name": provider, "url": "https://" + key, "recommended": True}]}
    return {"state": "draft", "author_ids": [aid], "draft": draft, "profile": "https://" + key,
            "note": "Public profile verified; original authorship and publication approval still pending"}
