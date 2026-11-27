"""Recover native Vita artwork from already verified VPKs, for review only."""
import hashlib
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 4 * 1024 * 1024
ICON = "sce_sys/icon0.png"
LIVEAREA = "sce_sys/livearea/contents/"
FALLBACKS = ("sce_sys/pic0.png", LIVEAREA + "bg.png", LIVEAREA + "bg0.png",
             LIVEAREA + "background.png", "sce_sys/bg.png")
MAX_SCREENSHOTS = 5


def template_backgrounds(z):
    """Only declared LiveArea background images, never gates/buttons or URLs."""
    try:
        item = z.getinfo(LIVEAREA + "template.xml")
        if not 0 < item.file_size <= 65536:
            return []
        raw = z.read(item)
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            return []
        root = ElementTree.fromstring(raw)
        found = []
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] not in {"livearea-background", "background"}:
                continue
            for image in node.iter():
                if image.tag.rsplit("}", 1)[-1] != "image":
                    continue
                name = (image.text or "").strip()
                if re.fullmatch(r"[A-Za-z0-9_. -]+\.png", name, re.IGNORECASE) and name not in {".", ".."}:
                    found.append(LIVEAREA + name)
        return found[:10]
    except (KeyError, ValueError, ElementTree.ParseError, OSError, RuntimeError, zipfile.BadZipFile):
        return []


def inspect_png(raw):
    if not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw) > MAX_IMAGE_BYTES:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as img:
            w, h = img.size
            if img.format != "PNG" or not (0 < w <= 4096 and 0 < h <= 4096 and w*h <= MAX_PIXELS):
                return None
            if getattr(img, "is_animated", False):
                return None
            img.verify()
        with Image.open(io.BytesIO(raw)) as img:
            img.load()
        return w, h
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError):
        return None


def extract_artwork(file, package_sha, output_dir):
    """Store exact original PNG bytes with safe digest-based names.

    The caller must have verified the whole package and its Title ID first.
    No public URLs are produced and missing artwork is not a VPK failure.
    """
    if not re.fullmatch(r"[a-f0-9]{64}", package_sha):
        raise ValueError("Expected verified VPK SHA-256")
    output_dir = Path(output_dir)
    found = {"version": 1, "icon": None, "screenshots": [], "skipped": []}
    with zipfile.ZipFile(file) as z:
        candidates = list(dict.fromkeys((ICON, *FALLBACKS, *template_backgrounds(z))))
        seen = set()
        for path in candidates:
            is_icon = path == ICON
            if not is_icon and len(found["screenshots"]) >= MAX_SCREENSHOTS:
                break
            try:
                info = z.getinfo(path)
            except KeyError:
                continue
            if not 0 < info.file_size <= MAX_IMAGE_BYTES:
                found["skipped"].append({"path": path, "reason": "size"})
                continue
            raw = z.read(info)
            dims = inspect_png(raw)
            if not dims:
                found["skipped"].append({"path": path, "reason": "invalid PNG or dimensions"})
                continue
            sha = hashlib.sha256(raw).hexdigest()
            if sha in seen:
                continue
            seen.add(sha)
            name = sha + ".png"
            output_dir.mkdir(parents=True, exist_ok=True)
            target = output_dir / name
            if not target.exists():
                target.write_bytes(raw)
            elif hashlib.sha256(target.read_bytes()).hexdigest() != sha:
                raise OSError("Existing artwork file does not match digest")
            meta = {"path": path, "artifact_file": name, "sha256": sha,
                    "vpk_sha256": package_sha, "size": len(raw),
                    "width": dims[0], "height": dims[1],
                    "kind": "icon" if is_icon else "pic0" if path.endswith("/pic0.png") else "livearea_background"}
            if is_icon:
                found["icon"] = meta
            else:
                found["screenshots"].append(meta)
    return found


def media_proposal(app, artwork, is_fallback):
    """Keep existing screenshot URLs first; only propose art when none exist."""
    shots = [x for x in app.get("screenshots", []) or [] if isinstance(x, str) and x.strip()]
    real_shots = [x for x in shots if not is_fallback(x)]
    icon = artwork.get("icon")
    available = max(0, MAX_SCREENSHOTS - len(shots))
    additions = artwork.get("screenshots", [])[:available] if not real_shots else []
    return {"icon": icon if not app.get("icon") else None,
            "existing_screenshots": shots[:MAX_SCREENSHOTS],
            "fallback_screenshots": additions,
            "requires_public_url": bool((icon and not app.get("icon")) or additions),
            "note": "VPK artwork is a fallback, not a gameplay screenshot; staged files are not public URLs"}
