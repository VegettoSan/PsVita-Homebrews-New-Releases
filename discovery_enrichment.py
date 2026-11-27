"""Review-only identification and taxonomy proposals for Orbit discoveries.

Never execute upstream code. No catalog or discovery state writes.
"""
from __future__ import annotations

import re
import struct
from pathlib import PurePosixPath

TITLE = r"[A-Z0-9]{9}"
KEY = r"(?:VITA_?TITLE_?ID|PSP2_?TITLE_?ID|APP_?TITLE_?ID|TITLE_?ID|UPDATER_?TITLE_?ID|TITLE[ \t]+ID)"
VALUE = r"[\x60\"']?(" + TITLE + r")[\x60\"']?(?![A-Za-z0-9_])"
DECLARATIONS = (
    re.compile(r"^\s*set\s*\(\s*(" + KEY + r")\s+" + VALUE, re.I),
    re.compile(r"^\s*(?:#\s*define\s+)?[\x60\"']?(" + KEY + r")[\x60\"']?\s*(?:[:?+]?=|:|\s)\s*" + VALUE, re.I),
    re.compile(r"^\s*\|\s*(?:\*\*)?(" + KEY + r")(?:\*\*)?\s*\|\s*" + VALUE + r"\s*\|", re.I),
    re.compile(r"^\s*<(" + KEY + r")>\s*" + VALUE + r"\s*</\1>", re.I),
    re.compile(r"(?:^|\s)-s\s+(" + KEY + r")=" + VALUE, re.I),
    re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*|__)?(" + KEY + r")(?:\*\*|__)?\s*:\s*(?:\*\*)?\s*" + VALUE, re.I),
)
EXCLUDED_DIRS = {"vendor", "third_party", "thirdparty", "node_modules", ".git", "library", "build", "dist", "obj", "bin", "packages", "tests", "test", "examples", "samples", "deps", "external"}
TEXT_SUFFIXES = {".md", ".txt", ".rst", ".cmake", ".mk", ".json", ".xml", ".ini", ".cfg", ".conf", ".yaml", ".yml", ".toml", ".c", ".cpp", ".h", ".hpp", ".sh", ".py", ".bat", ".ps1", ".asset", ".cs", ".lua", ".properties"}


def file_priority(path):
    """Rank relevant files anywhere in the tree; ignore vendored/generated paths."""
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or any(x.casefold() in EXCLUDED_DIRS for x in p.parts[:-1]):
        return None
    n = p.name.casefold()
    if n == "param.sfo":
        return 0
    if n in {"cmakelists.txt", "makefile", "gnumakefile", "projectsettings.asset"}:
        return 1
    if re.search(r"title.?id|param|manifest|version|config|build|package|vita|psp2", n) and p.suffix.casefold() in TEXT_SUFFIXES:
        return 2
    if n.startswith(("readme", "changelog", "release", "install")):
        return 3
    if p.suffix.casefold() in TEXT_SUFFIXES:
        return 4
    return None


def prioritized_paths(paths):
    ranked = [(file_priority(p), p) for p in set(paths) if isinstance(p, str)]
    return [p for rank, p in sorted((r, p) for r, p in ranked if r is not None)]


def sfo_title_id(raw):
    """Parse the PSF key table with bounds checks; never unpack/execute a VPK."""
    if not isinstance(raw, bytes) or not 20 <= len(raw) <= 262144 or raw[:4] != b"\x00PSF":
        return ""
    try:
        _, keys, values, count = struct.unpack_from("<IIII", raw, 4)
        if count > 1024 or not 20 + count * 16 <= keys <= values <= len(raw):
            return ""
        hits = []
        for i in range(count):
            ko, fmt, size, capacity, off = struct.unpack_from("<HHIII", raw, 20 + 16 * i)
            if not keys <= keys + ko < values:
                return ""
            end = raw.find(b"\0", keys + ko, values)
            if end < 0:
                return ""
            if raw[keys + ko:end] != b"TITLE_ID":
                continue
            if fmt != 0x0204 or not 1 <= size <= capacity or values + off + capacity > len(raw):
                return ""
            value = raw[values + off:values + off + size].rstrip(b"\0").decode("ascii")
            if not re.fullmatch(TITLE, value):
                return ""
            hits.append(value)
        return hits[0] if len(hits) == 1 else ""
    except (struct.error, UnicodeError):
        return ""


def text_evidence(text, source, kind, ref=""):
    """Explicit labelled literals only. Bare nine-character tokens never qualify."""
    out = []
    for lineno, line in enumerate(text[:262144].splitlines(), 1):
        # Ignore comments in build/code files, except C preprocessor definitions.
        stripped = line.lstrip()
        if kind == "code" and (stripped.startswith(("//", "/*", "*", ";")) or
                               (stripped.startswith("#") and not re.match(r"#\s*define\b", stripped))):
            continue
        for pattern in DECLARATIONS:
            for hit in pattern.finditer(line):
                key, tid = hit.group(1), hit.group(2).upper()
                confidence = "high" if kind == "build" and "updater" not in key.casefold() else "medium"
                out.append({"title_id": tid, "source": source, "line": lineno,
                            "ref": ref, "kind": kind, "field": key, "confidence": confidence})
    return out


def resolve_title(evidence, official_ids=(), retired_ids=()):
    """Never choose one of several distinct IDs or claim package verification."""
    unique = {(e["title_id"], e["source"], e.get("line"), e.get("ref")): e for e in evidence}
    rows = list(unique.values())
    ids = sorted({e["title_id"] for e in rows})
    if not ids:
        return {"state": "missing", "suggestion": "", "evidence": rows}
    if len(ids) > 1:
        return {"state": "ambiguous", "suggestion": "", "alternatives": ids, "evidence": rows}
    tid = ids[0]
    if tid in {str(x).upper() for x in retired_ids}:
        state = "retired"
    elif tid in {str(x).upper() for x in official_ids}:
        state = "collision"
    elif any(e["confidence"] == "high" for e in rows):
        state = "high"
    else:
        state = "medium"
    return {"state": state, "suggestion": tid if state in {"high", "medium"} else "",
            "alternatives": ids, "evidence": rows}


# These are evidence rules, not a second taxonomy. All resulting IDs are checked
# against the live official taxonomy. "port" alone does not mean a game port.
RULES = {
    "ports": [(8, r"\b(?:game port|port of (?:the (?:game|original) |an? (?:game|video game) )|source port of|ported (?:game|engine)|game (?:ported|port) to)\b"),
              (5, r"\b(?:port of|ported from|source port|engine port)\b")],
    "games": [(6, r"\b(?:homebrew game|original game|puzzle game|platformer game|video game|game developed for|game for (?:the )?ps vita)\b"),
              (3, r"\b(?:platformer|shooter|racing game|rpg|arcade game|visual novel)\b")],
    "emulators": [(10, r"\b(?:emulator|emulation frontend|emulation core)\b")],
    "plugins": [(10, r"\b(?:taihen plugin|kernel plugin|user plugin|vita plugin|skprx|suprx)\b"), (5, r"\bplugin\b")],
    "utilities": [(8, r"\b(?:file manager|save manager|calculator|backup tool|installer|terminal emulator|text editor|launcher|updater|utility)\b")],
    "system": [(8, r"\b(?:firmware tool|downgrade|system manager|mount tool|system diagnostics|system information|storage manager)\b")],
    "media": [(8, r"\b(?:music player|video player|image viewer|image editor|audio player|multimedia player|media player)\b")],
    "networking": [(8, r"\b(?:ftp server|ssh client|ssh server|web browser|remote desktop|game streaming|network tool|http server|messaging client)\b")],
    "customization": [(8, r"\b(?:theme manager|custom theme|wallpaper|boot animation|bubble manager|font manager)\b")],
    "modding": [(9, r"\b(?:translation patch|game patch|rom hack|texture pack|fan translation|localization patch|game mod)\b")],
    "development": [(8, r"\b(?:development library|software development kit|development tool|programming framework|compiler|debugger|game engine library)\b")],
    "demos": [(8, r"\b(?:tech demo|technical demo|proof of concept|benchmark|graphics demo|gameplay demo)\b")],
    "education": [(8, r"\b(?:educational app|learning tool|language learning|encyclopedia|math learning)\b")],
    "accessibility": [(8, r"\b(?:accessibility tool|assistive tool|screen reader|assistive technology)\b")],
    "preservation": [(8, r"\b(?:preservation tool|software preservation|archival tool|historical archive)\b")],
}
SUB_RULES = {
    "ports": {"fps": r"\b(?:fps|first.person shooter)\b", "racing": r"\b(?:racing|kart racing)\b", "platformer": r"\bplatformer\b", "puzzle": r"\bpuzzle\b", "rpg": r"\b(?:rpg|role.playing)\b", "strategy": r"\bstrategy\b", "source-port": r"\bsource port\b", "engine-port": r"\bengine port\b", "shooter": r"\bshooter\b", "action": r"\baction game\b", "adventure": r"\badventure game\b", "arcade": r"\barcade\b"},
    "games": {"platformer": r"\bplatformer\b", "puzzle": r"\bpuzzle\b", "rpg": r"\b(?:rpg|role.playing)\b", "racing": r"\bracing\b", "shooter": r"\bshooter\b", "fighting": r"\bfighting game\b", "horror": r"\bhorror\b", "visual-novel": r"\bvisual novel\b", "strategy": r"\bstrategy\b", "arcade": r"\barcade\b", "rhythm": r"\brhythm game\b", "action": r"\baction game\b"},
    "emulators": {"game-boy-advance": r"\b(?:gba|game boy advance)\b", "game-boy-color": r"\b(?:gbc|game boy color)\b", "snes": r"\b(?:snes|super nintendo)\b", "nes": r"\b(?:nes|famicom)\b", "nintendo-64": r"\b(?:n64|nintendo 64)\b", "multi-system": r"\b(?:multi.system|multiple systems)\b", "playstation": r"\b(?:ps1|psx)\b", "genesis": r"\b(?:genesis|mega drive)\b"},
    "plugins": {"overclock": r"\boverclock\w*\b", "performance": r"\b(?:performance|overclock\w*)\b", "audio": r"\baudio\b", "graphics": r"\bgraphics\b", "storage": r"\bstorage\b", "input": r"\b(?:input|controller)\b", "display": r"\bdisplay\b"},
    "utilities": {"file-manager": r"\bfile manager\b", "ftp": r"\bftp\b", "save-manager": r"\bsave manager\b", "calculator": r"\bcalculator\b", "installer": r"\binstaller\b", "launcher": r"\blauncher\b", "terminal": r"\bterminal\b", "text-editor": r"\btext editor\b", "backup": r"\bbackup\b", "updater": r"\bupdater\b"},
    "networking": {"ftp": r"\bftp\b", "web-browser": r"\bweb browser\b", "game-streaming": r"\bgame streaming\b", "remote-desktop": r"\bremote desktop\b", "client": r"\bclient\b", "server": r"\bserver\b"},
    "media": {"music-player": r"\bmusic player\b", "video-player": r"\bvideo player\b", "image-viewer": r"\bimage viewer\b", "image-editor": r"\bimage editor\b"},
    "modding": {"translation": r"\b(?:translation|fan translation)\b", "game-patches": r"\bgame patch\b", "rom-hacks": r"\brom hack\b", "textures": r"\btexture pack\b"},
    "system": {"firmware-tools": r"\bfirmware tool\b", "downgrade": r"\bdowngrade\b", "storage": r"\bstorage manager\b"},
    "customization": {"themes": r"\btheme\b", "wallpapers": r"\bwallpaper\b", "boot-animation": r"\bboot animation\b"},
}


def classify_category(name, description, readme, project_type, categories):
    taxonomy = {c["id"]: {s["id"] for s in c.get("subcategories", [])}
                for c in categories if isinstance(c, dict) and "id" in c}
    primary = (str(name) + "\n" + str(description)).replace("_", " ").replace("-", " ")
    body = str(readme)[:12000].replace("_", " ").replace("-", " ")
    scores, evidence = {}, {}
    for cid, rules in RULES.items():
        if cid not in taxonomy:
            continue
        for weight, pattern in rules:
            m = re.search(pattern, primary, re.I)
            origin = "metadata"
            if not m:
                m = re.search(pattern, body, re.I)
                origin = "readme"
            if m:
                scores[cid] = scores.get(cid, 0) + weight + (2 if origin == "metadata" else 0)
                evidence.setdefault(cid, []).append({"match": m.group(0), "source": origin})
    for kind, cid in (("plugin", "plugins"), ("patch", "modding")):
        if project_type == kind and cid in taxonomy:
            scores[cid] = scores.get(cid, 0) + 12
            evidence.setdefault(cid, []).append({"match": kind, "source": "existing target classifier"})
    # A generic port claim is insufficient without evidence of a game/engine.
    if scores.get("ports", 0) and not (scores.get("games", 0) or any(
            re.search(r"\b(?:game port|game ported|ported game|engine port|source port of)\b", e["match"], re.I)
            for e in evidence.get("ports", []))):
        scores.pop("ports", None)
    # A game port may contain genre terms used by the original-games rules.
    if "ports" in scores and "games" in scores:
        scores["ports"] += scores.pop("games")
    ranked = sorted(scores, key=lambda k: (-scores[k], k))
    if not ranked or scores[ranked[0]] < 6:
        return {"state": "missing", "category_id": "", "subcategory_ids": [], "scores": scores, "evidence": evidence}
    cid = ranked[0]
    if len(ranked) > 1 and scores[cid] - scores[ranked[1]] < 3:
        return {"state": "ambiguous", "category_id": "", "subcategory_ids": [], "scores": scores, "evidence": evidence}
    subs = [sid for sid, pattern in SUB_RULES.get(cid, {}).items()
            if sid in taxonomy[cid] and re.search(pattern, primary + "\n" + body, re.I)]
    return {"state": "suggested", "category_id": cid, "subcategory_ids": subs[:5],
            "scores": scores, "evidence": evidence}
