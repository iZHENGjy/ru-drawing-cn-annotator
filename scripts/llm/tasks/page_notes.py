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
    tail = marks_line(d) + terms_block(d.get("terms"))
    return "\n".join(out) + ("\n\n" + tail if tail else ""), imgs


def validate(payload, output):
    return validate_page_notes(payload, output, SCHEMA)


def fake(payload):
    return {"sheet_code": "", "format": "", "title_ru": "", "title_zh": "", "notes": [], "labels": [],
            "issues": ["fake backend: no reading"]}
