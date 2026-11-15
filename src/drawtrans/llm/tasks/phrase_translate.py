"""phrase_translate — a batch (~60) of deduplicated OCR phrases with numbers as ⟨n⟩.

payload["data"] = {
  "items": [{"id": str, "key": "Арматура А⟨1⟩С", "sample": "Арматура А500С",
             "ctx": ["line above", "line below"], "n": 3}, ...],
  "terms": {ru: zh} | [[ru, zh], ...]            # optional TM / glossary hits
}
answer = {"items": [{"id", "action": translate|keep|skip, "zh", "conf": high|low}]}
"""
from __future__ import annotations

from ..validate import bad_ids, validate_phrase_translate
from ._common import dumps, terms_block

NAME = "phrase_translate"
PROMPT_VERSION = "2"
SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"},
                       "action": {"type": "string", "enum": ["translate", "keep", "skip"]},
                       "zh": {"type": "string"},
                       "conf": {"type": "string", "enum": ["high", "low"]}},
        "required": ["id", "action", "zh", "conf"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False}


def render(payload):
    d = payload["data"]
    lines = []
    for it in d["items"]:
        o = {"id": str(it["id"]), "key": it["key"]}
        if it.get("sample") and it["sample"] != it["key"]:
            o["sample"] = it["sample"]
        ctx = [c for c in (it.get("ctx") or []) if c]
        if ctx:
            o["ctx"] = it["ctx"]
        if it.get("n", 1) > 1:
            o["n"] = it["n"]
        lines.append(dumps(o))
    return terms_block(d.get("terms")) + "Items:\n" + "\n".join(lines), []


def validate(payload, output):
    return validate_phrase_translate(payload, output, SCHEMA)


def fake(payload):
    return {"items": [{"id": str(it["id"]), "action": "keep", "zh": "", "conf": "low"}
                      for it in payload["data"]["items"]]}


def retry_payload(payload, output, errors):
    ids = bad_ids(errors)
    if not ids:
        return None
    sub = [it for it in payload["data"]["items"] if str(it["id"]) in ids]
    if not sub or len(sub) == len(payload["data"]["items"]):
        return None
    return {"data": {**payload["data"], "items": sub}, "images": []}


def merge(prev, new):
    by = {o["id"]: o for o in prev.get("items", [])}
    for o in (new or {}).get("items", []):
        if isinstance(o, dict) and "id" in o:
            by[o["id"]] = o
    return {"items": list(by.values())}
