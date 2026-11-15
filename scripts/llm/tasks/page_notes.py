"""page_notes — one sheet: title-block crop + notes crops (images) with their OCR lines.

payload["data"] = {                       # as built by scripts/scan/packets.py page_notes()
  "page": 7,
  "regions": [
    {"kind": "title_block", "images": [0], "ocr": ["line", ...]},
    {"kind": "notes" | "notes_en", "images": [1, 2], "ocr": [...], "clauses": 6},
    {"kind": "number_check", "images": [3], "items": [{"n": 1, "tess": "...", "rapid": "..."}]},
    ...],                                 # "images" = indices into payload["images"];
                                          # without it, regions[i] <-> images[i]
  "expect_notes": true,                   # optional
  "marks": ["Км1.1", ...],                # optional: known member marks (sheet titles)
  "terms": {ru: zh} | [[ru, zh]] | [{"ru", "zh"}]   # optional
}
answer = {sheet_code, format, title_ru, title_zh, notes: [{no, ru, zh}], labels: [{ru, zh}], issues: [str]}
notes[].ru is the FULL clause as read from the image (the digit check of zh runs against it);
number_check snippets come back as labels (ru exactly as read in the image).
"""
from __future__ import annotations

from ..validate import validate_page_notes
from ._common import marks_line, terms_block

NAME = "page_notes"
PROMPT_VERSION = "1"
_S = {"type": "string"}
SCHEMA = {
    "type": "object",
    "properties": {
        "sheet_code": _S, "format": _S, "title_ru": _S, "title_zh": _S,
        "notes": {"type": "array", "items": {"type": "object", "properties": {"no": _S, "ru": _S, "zh": _S},
                                             "required": ["no", "ru", "zh"], "additionalProperties": False}},
        "labels": {"type": "array", "items": {"type": "object", "properties": {"ru": _S, "zh": _S},
                                              "required": ["ru", "zh"], "additionalProperties": False}},
        "issues": {"type": "array", "items": _S}},
    "required": ["sheet_code", "format", "title_ru", "title_zh", "notes", "labels", "issues"],
    "additionalProperties": False}

KIND = {"title_block": "title block", "notes": "notes (Russian)", "notes_en": "notes (English)",
        "number_check": "number check"}


def render(payload):
    d = payload["data"]
    imgs = list(payload.get("images") or [])
    out = [f"Sheet {d.get('page', '')}: {len(imgs)} image(s) attached, numbered 1..{len(imgs)} in order."]
    if d.get("expect_notes") is False:
        out.append("No notes block was detected on this sheet (notes may be empty).")
    for i, r in enumerate(d.get("regions") or []):
        idx = r.get("images")
        idx = [i] if idx is None else list(idx)
        where = ", ".join(f"image {k + 1}" for k in idx) or "no image"
        kind = r.get("kind", "region")
        head = f"\n[{KIND.get(kind, kind)}: {where}]"
        if r.get("clauses"):
            head += f" — about {r['clauses']} numbered clauses detected"
        out.append(head)
        if kind == "number_check":
            out.append("Snippets numbered in the image; the two OCR engines disagree on their numbers. "
                       "Read each snippet and add it to labels (ru exactly as printed, zh its Chinese):")
            out += [f"  {x.get('n')}: OCR-1 «{x.get('tess', '')}» / OCR-2 «{x.get('rapid', '')}»"
                    for x in r.get("items") or []]
            continue
        out.append("OCR text (hint only):")
        out += [f"  {ln}" for ln in r.get("ocr") or []]
    if d.get("known"):           # fixed texts of the volume (title-block fields, table headers) already in the TM
        out.append("")
        out.append("Already translated in this volume (leave them out of labels): " + "; ".join(d["known"]))
    tail = marks_line(d) + terms_block(d.get("terms"))
    return "\n".join(out) + ("\n\n" + tail if tail else ""), imgs


def validate(payload, output):
    return validate_page_notes(payload, output, SCHEMA)


def fake(payload):
    return {"sheet_code": "", "format": "", "title_ru": "", "title_zh": "", "notes": [], "labels": [],
            "issues": ["fake backend: no reading"]}


def normalize(payload, output):
    """Mechanical fixes before validation (each one used to cost a full retry of the sheet):
    format «А3х3» / «A1 (594x841)» -> A3x3 / A1; decimal commas in Chinese -> points.
    With [job] optimize, labels that still break a code rule are dropped (the audit re-checks the sheet)."""
    import re
    from ..validate import DEC_COMMA, check_against
    out = dict(output)
    fmt = str(out.get("format") or "").strip()
    if fmt and not re.fullmatch(r"([AАaа][0-4](\s?[xх×]\s?\d+)?)?", fmt):
        m = re.search(r"[AА]\s?([0-4])(?:\s?[xх×]\s?(\d+))?", fmt)
        out["format"] = ("A" + m.group(1) + ("x" + m.group(2) if m.group(2) else "")) if m else ""

    import i18n
    keep_comma = i18n.pair().tgt.code not in ("zh", "en")      # ru / de / fr targets write decimal commas

    def fixz(z):
        if keep_comma:
            return z
        return DEC_COMMA.sub(lambda mm: mm.group(1).replace(",", "."), z or "") if isinstance(z, str) else z
    for k in ("title_zh",):
        if isinstance(out.get(k), str):
            out[k] = fixz(out[k])
    for lst in ("notes", "labels"):
        if isinstance(out.get(lst), list):
            out[lst] = [dict(x, zh=fixz(x.get("zh"))) if isinstance(x, dict) else x for x in out[lst]]
    if payload.get("drop_bad_labels") and isinstance(out.get("labels"), list):
        marks = payload.get("data", {}).get("marks") or ()
        out["labels"] = [lb for lb in out["labels"] if not (isinstance(lb, dict) and lb.get("zh") and
                                                            check_against(lb.get("zh", ""), lb.get("ru", ""), marks))]
    return out
