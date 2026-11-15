"""visual_review — tiles of a finished page flagged by QA (occlusion, small font, leaders, changed).

payload["data"] = {
  "page": 7,
  "tiles": [{"id": "t2", "ocr": ["OCR lines inside the tile", ...], "flag": "why QA flagged it"}, ...]
           or the tiles.json mapping {"p07_t2": [x0, y0, x1, y1], ...} (ids become "t2"),
  "flags": ["QA flag text", ...],      # optional, page level
  "marks": ["Км1.1", ...],             # optional
  "count_word": "根"                   # optional, project's шт./pcs. label
}
payload["images"][i] is the PNG of tiles[i].  Without "ocr" a tile has no confirmed numbers, so any
number in a suggested zh is rejected (digit guard): pass the OCR lines of each tile when available.
answer = {"checked": [tile ids], "issues": [{tile, type, text, where:[x0,y0,x1,y1], zh}]}
-> work/visual/r<N>/pNN.json for 41_apply_visual.py.
"""
from __future__ import annotations

from pathlib import Path

from ..validate import tiles_of, validate_visual_review
from ._common import marks_line

NAME = "visual_review"
PROMPT_VERSION = "1"
_S = {"type": "string"}
SCHEMA = {
    "type": "object",
    "properties": {
        "checked": {"type": "array", "items": _S},
        "issues": {"type": "array", "items": {
            "type": "object",
            "properties": {"tile": _S, "type": {"type": "string", "enum": ["漏译", "英文无中文", "遮挡", "错译"]},
                           "text": _S, "where": {"type": "array", "items": {"type": "number"}}, "zh": _S},
            "required": ["tile", "type", "text", "where", "zh"], "additionalProperties": False}}},
    "required": ["checked", "issues"], "additionalProperties": False}


def render(payload):
    d = payload["data"]
    imgs = [Path(p) for p in payload.get("images") or []]
    out = [f"Page {d.get('page', '')}. Tiles, images attached in the same order:"]
    if d.get("flags"):
        out.append("QA flags for this page: " + "; ".join(map(str, d["flags"])))
    for i, t in enumerate(tiles_of(d)):
        out.append(f"\n[image {i + 1}] tile {t['id']}" + (f" — flagged: {t['flag']}" if t.get("flag") else ""))
        if "ocr" in t:
            out.append("OCR text in this tile: " + (" | ".join(t["ocr"]) or "(none)"))
    if d.get("count_word"):
        out.append(f"\nCount marks шт./pcs.N are labelled «{d['count_word']}», ш./sp.N «间距» by the engine.")
    tail = marks_line(d)
    return "\n".join(out) + ("\n" + tail if tail else ""), imgs


def validate(payload, output):
    return validate_visual_review(payload, output, SCHEMA)


def fake(payload):
    return {"checked": [t["id"] for t in tiles_of(payload["data"])], "issues": []}
