"""Backward-compatible reader for PSVitaAlive's public V1 and V2 JSON fields.

This small adapter intentionally only affects data read from PSVitaAlive catalog;
GitHub/GitLab API responses are never transformed.
"""
KEYS = {
  "title_id": "tid",
  "title_ids": "tids",
  "name": "n",
  "description": "d",
  "long_description": "ld",
  "author_ids": "aids",
  "author_id": "aid",
  "category_id": "cid",
  "subcategory_ids": "sids",
  "version": "v",
  "version_date": "vd",
  "release_date": "rd",
  "updated_at": "ua",
  "date": "dt",
  "icon": "ic",
  "icon_url": "icu",
  "cover": "cv",
  "cover_url": "cvu",
  "background": "bg",
  "background_url": "bgu",
  "screenshots": "ss",
  "screenshot_urls": "ssu",
  "links": "l",
  "status": "st",
  "source": "so",
  "source_name": "sn",
  "source_id": "si",
  "source_url": "su",
  "source_catalog": "sct",
  "source_labels": "sl",
  "sources": "srcs",
  "release_page": "rp",
  "requirements": "req",
  "changelog": "cl",
  "downloads": "dl",
  "download_url": "dlu",
  "hash": "h",
  "hash2": "h2",
  "score": "sc",
  "size": "sz",
  "data_size": "dsz",
  "data_url": "du",
  "data_extract_path": "dxp",
  "data_size_bytes": "dsb",
  "game_data_languages": "gdl",
  "languages": "langs",
  "language": "lang",
  "extract_path": "xp",
  "installed_paths": "ips",
  "recommended": "rec",
  "type": "t",
  "url": "u",
  "avatar": "av",
  "bio": "b",
  "order": "ord",
  "subcategories": "subs",
  "content_id": "coid",
  "contentId": "coi",
  "region": "rg",
  "regions": "rgs",
  "zrif": "zr",
  "zRIF": "zR",
  "license": "lic",
  "required": "rq",
  "section": "sec",
  "line": "ln",
  "plugin_install_path": "pip",
  "plugin_config_section": "pcs",
  "plugin_config_line": "pcl",
  "plugins": "plg",
  "conflicts_with": "cwf",
  "replaces": "repl",
  "kernel_priority": "kp",
  "reboot_required": "rr",
  "last_seen": "ls",
  "original_name": "on",
  "overview": "ov",
  "install_count": "inct",
  "average_rating": "ar",
  "ratings_count": "rc",
  "likes_count": "lc",
  "comments_count": "cc",
  "user_liked": "ul",
  "user_rating": "ur"
}
REVERSE = {short: long for long, short in KEYS.items()}
DYNAMIC = {"sources", "title_ids", "overview"}


def to_v1(data, dynamic=False):
    if isinstance(data, list):
        return [to_v1(x, dynamic) for x in data]
    if not isinstance(data, dict):
        return data
    out = {}
    for key, value in data.items():
        long_key = key if dynamic else REVERSE.get(key, key)
        if long_key in out:
            raise ValueError(f"V2 duplicate key: {long_key}")
        out[long_key] = to_v1(value, not dynamic and long_key in DYNAMIC)
    return out
