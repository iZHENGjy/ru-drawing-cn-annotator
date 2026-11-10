"""audit_review — one coverage-audit packet of scripts/scan/30_audit.py (pNN.md + pNN_sK.png).

payload["data"] = {
  "packet": "<text of work/packets/pNN.md>",
  "refs": ["#1", "#2", "C1", ...],     # optional; default: parsed from the packet (= cands.json keys)
  "marks": ["Км1.1", ...],             # optional: known member marks (sheet titles)
  "terms": {ru: zh} | [[ru, zh], ...]  # optional
}
payload["images"] = the crop sheets pNN_s1.png, pNN_s2.png ... (order of the packet).
answer = {"items": [{ref, act: tr|ok|skip, ru, zh, why}]}  -> work/answers/audit/pNN.json
(31_apply_audit.py reads exactly this; unused fields are "").
"""
from __future__ import annotations

import re
from pathlib import Path

from ..validate import audit_refs, validate_audit_review
from ._common import marks_line, terms_block

NAME = "audit_review"
PROMPT_VERSION = "1"
_S = {"type": "string"}
SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"ref": _S, "act": {"type": "string", "enum": ["tr", "ok", "skip"]},
                       "ru": _S, "zh": _S, "why": _S},
        "required": ["ref", "act", "ru", "zh", "why"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False}


def render(payload):
    d = payload["data"]
    imgs = [Path(p) for p in payload.get("images") or []]
    # crop-sheet lines carry local paths: keep only the file name
    md = re.sub(r"(裁图联排:\s*)(\S.*?)(\s+\(含)", lambda m: m.group(1) + re.split(r"[\\/]", m.group(2))[-1] + m.group(3),
                d["packet"])
    head = ""
    if imgs:
        head = "Images attached in this order: " + ", ".join(p.name for p in imgs) + "\n"
    refs = audit_refs(d)
    tail = marks_line(d) + terms_block(d.get("terms"))
    return (head + "Packet:\n" + md.strip() + "\n\nAnswer these refs: " + ", ".join(refs)
            + ("\n" + tail if tail else "")), imgs


def validate(payload, output):
    return validate_audit_review(payload, output, SCHEMA)


def fake(payload):
    return {"items": [{"ref": r, "act": "skip", "ru": "", "zh": "", "why": "fake backend"}
                      for r in audit_refs(payload["data"])]}
