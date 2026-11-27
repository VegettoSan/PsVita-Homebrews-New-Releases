"""Conservative extraction of user-facing runtime requirements (not build dependencies)."""
import re

NO_DATA = "- no data"
HEADER = re.compile(r"^(?:#{1,6}\s+)?(?:\*\*)?((?:(?:runtime|system|game|minimum|additional)\s+)?requirements|prerequisites|requisitos|installation|install|setup|how to install|dependencies)(?:\*\*)?\s*:?\s*$", re.I)
BUILD = re.compile(r"\b(?:build(?:ing)?|compil(?:e|ing|ation)|development|developer|source code|vitasdk|cmake|makefile|toolchain|pip install|npm install|apt install|git clone|compiler|linker)\b", re.I)
RUNTIME = re.compile(r"\b(?:firmware|henkaku|enso|kubridge|fd_fix|fdfix|shacccg|nonpdrm|repatch|psm runtime|plugin[s]?|game (?:files|data|assets|copy)|data files|original (?:game|files|assets|copy)|storage|free space|memory card|internet connection|wi-fi|wifi|bios|roms?)\b|(?:ux0|ur0|uma0):", re.I)
NECESSITY = re.compile(r"\b(?:requires?|required|must|needs?|necessary|mandatory|install|copy|place|extract|put|enable|make sure|have)\b", re.I)
OPTIONAL = re.compile(r"\b(?:optional|not required|no need|not necessary|recommended but|if you want)\b", re.I)
NONE = re.compile(r"^(?:none|no (?:additional |special )?requirements|no (?:additional )?plugins required|no additional dependencies)[.!]?$", re.I)


def extract_requirements(text, source, ref=""):
    rows = []
    section = ""
    section_level = 0
    fenced = False
    for number, raw in enumerate(str(text or "")[:262144].splitlines(), 1):
        line = raw.strip()
        if line.startswith(("\x60\x60\x60", "~~~")):
            fenced = not fenced
            continue
        if fenced or not line:
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        match = HEADER.fullmatch(line)
        if match:
            label = match.group(1).casefold()
            section = "install" if label in {"installation", "install", "setup", "how to install"} else "requirements"
            section_level = len(heading.group(1)) if heading else 0
            continue
        if heading:
            level = len(heading.group(1))
            if BUILD.search(heading.group(2)):
                section = "build"
                section_level = level
            elif not section_level or level <= section_level:
                section = ""
                section_level = level
            continue
        line = re.sub(r"^(?:[-*+]\s+|\d+\.\s+)", "", line)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 (\2)", line)
        line = re.sub(r"[*\x60]", "", line).strip()
        inline = re.match(r"^(?:(?:runtime|system|game)\s+)?requirements?\s*:\s*(.+)$", line, re.I)
        if inline:
            line = inline.group(1).strip()
        if not line or len(line) > 500 or line.startswith(("!", "<", "|")) or section == "build" or BUILD.search(line) or OPTIONAL.search(line):
            continue
        explicit_none = bool(NONE.fullmatch(line)) and (section == "requirements" or inline)
        runtime = RUNTIME.search(line) and (section == "requirements" or inline or NECESSITY.search(line))
        if explicit_none or runtime:
            rows.append({"text": line, "source": source, "line": number, "ref": ref,
                         "explicit_none": bool(explicit_none)})
    return rows


def resolve_requirements(evidence):
    unique = {}
    for row in evidence:
        unique.setdefault(row["text"].casefold(), row)
    rows = list(unique.values())
    if not rows:
        return {"state": "no_data", "value": NO_DATA, "evidence": []}
    positive = [r for r in rows if not r["explicit_none"]]
    # Preserve contradictory documentation for review, never claim no requirements.
    if positive and any(r["explicit_none"] for r in rows):
        state = "review"
    else:
        state = "detected" if positive else "explicit_none"
    chosen = positive or rows
    return {"state": state, "value": "\n".join("- " + r["text"] for r in chosen[:12]),
            "evidence": rows[:60]}


def relevant_document(path):
    """Root docs or project documentation folders; not dependency manifests."""
    parts = path.casefold().split("/")
    name = parts[-1]
    return (len(parts) == 1 or parts[0] in {"docs", "doc", "documentation"}) and (
        name.startswith(("readme", "install", "requirements", "setup")) and
        name.endswith((".md", ".rst", ".txt")) and name != "requirements.txt")
