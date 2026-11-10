"""Code checks applied to every LLM answer (a failing answer is retried with the error list,
never silently accepted).

Error strings that concern one item start with ``[<id>] `` so the runner can retry just those
items (``bad_ids``).  The guards follow the digit rule of the pipeline: Chinese never contains a
number, member mark, steel grade or standard number that is not in its source text.
"""
from __future__ import annotations

import re

from engine.tm import NUM, PH, TM

CJK = re.compile(r"[㐀-鿿]")
CYR_RUN = re.compile(r"[А-Яа-яЁё]+")
# member marks — same expression as scripts/scan/50_check.py (MARK)
MARK = re.compile(r"([А-ЯЁ]м|МН)\s?(\d+(?:\.\d+)?(?:-\d+)?)")
# rebar / structural steel grades: А240, А500С, В500С, С345, С345-5, С255Б ...
GRADE = re.compile(r"(?<![A-Za-zА-Яа-яЁё\d])([АAВBСC]\s?\d{3}(?:[СCНHТTБ])?(?:-\d)?)(?!\d)")
# standards: ГОСТ 34028-2016, ГОСТ Р 52544-2006, СП 63.13330.2018, СНиП 2.03.01-84, ТУ 14-1-5254
STD = re.compile(r"(?<![A-Za-zА-Яа-яЁё])(ГОСТ|GOST|СНиП|СП|SP|ТУ|TU)\s*(?:Р\s*|R\s*)?(\d[\d.\-–]*\d|\d)")
COUNT_ONLY = re.compile(r"/?\s*(\d[\d+()]*\s*|⟨\d+⟩\s*)?(根|件|个|间距\s*(\d*|⟨\d+⟩))")
DEC_COMMA = re.compile(r"(?<![\d,])(\d+,\d+)(?![\d,])(?!-[A-Za-zА-Яа-яЁё])")
MONTHS = {"январ": "1", "феврал": "2", "март": "3", "апрел": "4", "мая": "5", "май": "5", "июн": "6",
          "июл": "7", "август": "8", "сентябр": "9", "октябр": "10", "ноябр": "11", "декабр": "12"}
_HOMO = str.maketrans("ABCEHKMOPTXaceopxy", "АВСЕНКМОРТХасеорху")
ITEM_ERR = re.compile(r"^\[([^\]]+)\] ")


def bad_ids(errors: list[str]) -> set[str] | None:
    """ids named in item-level errors; None if any error is global (retry everything)."""
    ids = set()
    for e in errors:
        m = ITEM_ERR.match(e)
        if not m:
            return None
        ids.add(m.group(1))
    return ids


def norm(s: str) -> str:
    """homoglyph- and space-insensitive form for code comparison (Latin A/C/... -> Cyrillic)."""
    return re.sub(r"\s+", "", (s or "").translate(_HOMO)).replace("–", "-")


def numbers(s: str) -> set[str]:
    """numbers of a text; decimal comma -> point; '1 200' also yields '1200'."""
    s = s or ""
    out = {n.replace(",", ".") for n in NUM.findall(s)}
    for m in re.finditer(r"\d{1,3}(?: \d{3})+(?![\d])", s):
        out.add(m.group(0).replace(" ", ""))
    return out


def has_cjk(s: str) -> bool:
    return bool(CJK.search(s or ""))


def check_against(zh: str, src: str, known_marks=()) -> list[str]:
    """Problems of a Chinese text with respect to its source text (no item prefix)."""
    errs = []
    zh = zh or ""
    srcn = norm(src)
    known = {norm(m) for m in known_marks or ()}
    # member marks
    src_marks = {norm(m.group(1) + m.group(2)) for m in MARK.finditer(src or "")}
    z_rest = zh
    for m in MARK.finditer(zh):
        tok = norm(m.group(1) + m.group(2))
        if tok in src_marks:
            continue
        if tok in known:
            z_rest = z_rest.replace(m.group(0), " ")      # confirmed by the sheet-title list
            continue
        errs.append(f"member mark {m.group(0)!r} is not in the source (OCR may drop the dot: copy exactly)")
    # grades / standards
    for m in GRADE.finditer(z_rest):
        if norm(m.group(1)) not in srcn:
            errs.append(f"grade {m.group(1)!r} is not in the source")
    src_std = {norm(m.group(2)) for m in STD.finditer(src or "")}
    for m in STD.finditer(z_rest):
        if norm(m.group(2)) not in src_std and norm(m.group(2)) not in srcn:
            errs.append(f"standard number {m.group(0)!r} is not in the source")
    # numbers
    allowed = numbers(src)
    low = (src or "").lower()
    months = {v for k, v in MONTHS.items() if k in low}
    extra = []
    for n in sorted(numbers(z_rest)):
        if n in allowed:
            continue
        if n in months and re.search(re.escape(n) + r"\s*月", z_rest):
            continue
        extra.append(n)
    if extra:
        errs.append(f"numbers {extra} in zh are not in the source text (copy numbers exactly, never add any)")
    m = DEC_COMMA.search(zh)
    if m:
        errs.append(f"decimal comma {m.group(1)!r} in zh: use a point")
    # Cyrillic: only copied codes
    for m in CYR_RUN.finditer(zh):
        w = m.group(0)
        if norm(w) not in srcn:
            errs.append(f"Russian {w!r} in zh is not in the source")
        elif len(w) >= 3 and w.islower():
            errs.append(f"untranslated Russian word {w!r} in zh")
    return errs


# --------------------------------------------------------------------------- schema shape
def shape(obj, schema: dict, path: str = "$") -> list[str]:
    """Minimal JSON-Schema check (type/properties/required/enum/items/additionalProperties)."""
    t = schema.get("type")
    tmap = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}
    if t and not isinstance(obj, tmap[t]) or (t in ("integer", "number") and isinstance(obj, bool)):
        return [f"{path}: expected {t}"]
    errs = []
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: {obj!r} not in {schema['enum']}")
    if t == "object":
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in obj:
                errs.append(f"{path}: missing field {k!r}")
        for k, v in obj.items():
            if k in props:
                errs += shape(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected field {k!r}")
    elif t == "array" and "items" in schema:
        for i, v in enumerate(obj):
            errs += shape(v, schema["items"], f"{path}[{i}]")
    return errs


def ids_complete(got: list, want: list, label: str = "id") -> list[str]:
    errs, seen = [], set()
    wantset = set(want)
    for g in got:
        if g in seen:
            errs.append(f"[{g}] duplicate {label}")
        elif g not in wantset:
            errs.append(f"unknown {label} {g!r} (answer only the given ones)")
        seen.add(g)
    errs += [f"[{w}] missing from the answer" for w in want if w not in seen]
    return errs


# --------------------------------------------------------------------------- tasks
def validate_phrase_translate(payload: dict, out: dict, schema: dict) -> list[str]:
    errs = shape(out, schema)
    if errs:
        return errs
    items = payload["data"]["items"]
    byid = {str(it["id"]): it for it in items}
    errs = ids_complete([str(o["id"]) for o in out["items"]], list(byid))
    for o in out["items"]:
        it = byid.get(str(o["id"]))
        if it is None or o["action"] != "translate":
            continue
        p = f"[{o['id']}] "
        zh, key = o["zh"].strip(), it["key"]
        if not has_cjk(zh):
            errs.append(p + "action translate needs Chinese in zh (use keep for codes/names)")
            continue
        e = TM.check_entry(key, zh)
        if e:
            errs.append(p + e + f" — zh must carry exactly the placeholders of key {sorted(PH.findall(key))}")
        if COUNT_ONLY.fullmatch(zh):
            errs.append(p + "count labels (根/件/间距) are written by the engine: use skip")
        for m in CYR_RUN.finditer(zh):
            w = m.group(0)
            if norm(w) not in norm(key):
                errs.append(p + f"Russian {w!r} in zh is not in key")
            elif sum(c.islower() for c in w) >= 3:          # names/words: action keep, not translate
                errs.append(p + f"untranslated Russian word {w!r} in zh")
    return errs


def validate_page_notes(payload: dict, out: dict, schema: dict) -> list[str]:
    errs = shape(out, schema)
    if errs:
        return errs
    marks = payload.get("data", {}).get("marks") or ()
    pairs = [("title", out["title_ru"], out["title_zh"])]
    pairs += [(f"note {n['no'] or i + 1}", (n["no"] + " " + n["ru"]).strip(), n["zh"]) for i, n in enumerate(out["notes"])]
    pairs += [(f"label {i + 1}", lb["ru"], lb["zh"]) for i, lb in enumerate(out["labels"])]
    for where, ru, zh in pairs:
        if ru.strip() and not zh.strip():
            errs.append(f"[{where}] empty zh")
            continue
        if where.startswith("label") and COUNT_ONLY.fullmatch(zh.strip()):
            errs.append(f"[{where}] count labels are written by the engine: leave them out")
        errs += [f"[{where}] {e}" for e in check_against(zh, ru, marks)]
    if re.search(r"[\"'«»]", out["sheet_code"]):
        errs.append(f"sheet_code {out['sheet_code']!r}: code only, no quotes (empty string if not visible)")
    if not re.fullmatch(r"([AАaа][0-4](\s?[xх×]\s?\d+)?)?", out["format"].strip()):
        errs.append(f"format {out['format']!r}: expected like A1, A3x3 or empty")
    blob = " ".join(x for _, a, b in pairs for x in (a, b))
    if "[?]" in blob and not out["issues"]:
        errs.append("[?] used but issues is empty: describe the unreadable text in issues")
    return errs


def packet_blocks(md: str) -> dict[str, str]:
    """ref (#3 / C2) -> its lines in a 30_audit packet, without box coordinates."""
    blocks, cur = {}, None
    for line in (md or "").splitlines():
        m = re.match(r"^(#\d+|C\d+)\s", line)
        if m:
            cur = m.group(1)
            blocks[cur] = line[m.end():]
        elif cur and line.startswith((" ", "\t")):
            blocks[cur] += "\n" + line
        else:
            cur = None
    return {k: re.sub(r"box=\[[^\]]*\]", " ", v) for k, v in blocks.items()}


def audit_refs(data: dict) -> list[str]:
    refs = data.get("refs")
    if refs:
        return [str(r) for r in refs]
    return list(packet_blocks(data.get("packet", "")))


def validate_audit_review(payload: dict, out: dict, schema: dict) -> list[str]:
    errs = shape(out, schema)
    if errs:
        return errs
    data = payload["data"]
    blocks = packet_blocks(data.get("packet", ""))
    marks = data.get("marks") or ()
    errs = ids_complete([o["ref"] for o in out["items"]], audit_refs(data), "ref")
    for o in out["items"]:
        p = f"[{o['ref']}] "
        if o["act"] in ("ok", "skip"):
            if not o["why"].strip():
                errs.append(p + f"act {o['act']} needs why")
            continue
        zh, ru = o["zh"].strip(), o["ru"].strip()
        if not has_cjk(zh):
            errs.append(p + "act tr needs Chinese in zh")
            continue
        if COUNT_ONLY.fullmatch(zh):
            errs.append(p + "count labels are written by the engine: use skip")
        src = blocks.get(o["ref"], data.get("packet", ""))
        errs += [p + e for e in check_against(zh, src, marks)]
        if ru:
            extra = sorted(numbers(zh) - numbers(ru) - set().union(*(numbers(m) for m in marks)))
            if extra and not any("numbers" in e for e in errs if e.startswith(p)):
                errs.append(p + f"numbers {extra} in zh are not in ru")
    return errs


def tiles_of(data: dict) -> list[dict]:
    """Tiles as [{id, ocr, flag}] in image order.  Accepts that list, or the tiles.json mapping
    {"p07_t2": [x0, y0, x1, y1, ...]} (ids shortened to "t2", the form 41_apply_visual expects)."""
    t = data.get("tiles") or []
    if isinstance(t, dict):
        t = [{"id": k} for k in t]
    out = []
    for x in t:
        x = dict(x) if isinstance(x, dict) else {"id": str(x)}
        m = re.fullmatch(r"p\d+_(t\d+)", str(x["id"]))
        x["id"] = m.group(1) if m else str(x["id"])
        out.append(x)
    return out


def validate_visual_review(payload: dict, out: dict, schema: dict) -> list[str]:
    errs = shape(out, schema)
    if errs:
        return errs
    data = payload["data"]
    tiles = {t["id"]: t for t in tiles_of(data)}
    marks = data.get("marks") or ()
    errs = ids_complete([str(c) for c in out["checked"]], list(tiles), "tile")
    for i, it in enumerate(out["issues"], 1):
        p = f"[issue {i}] "
        t = tiles.get(str(it["tile"]))
        if t is None:
            errs.append(p + f"unknown tile {it['tile']!r}")
            continue
        w = it["where"]
        if len(w) != 4 or not all(0 <= v <= 1 for v in w) or w[0] >= w[2] or w[1] >= w[3]:
            errs.append(p + "where must be [x0,y0,x1,y1] fractions 0-1 inside the tile, x0<x1, y0<y1")
        if it["zh"].strip():
            src = "\n".join(t.get("ocr") or [])
            errs += [p + e for e in check_against(it["zh"], src, marks)]
    return errs
