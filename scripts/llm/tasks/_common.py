from __future__ import annotations

import json
from pathlib import Path


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def terms_block(terms) -> str:
    """terms: {ru: zh} | [[ru, zh], ...] | [{"ru":..,"zh":..}, ...] -> 'ru → zh' lines."""
    if not terms:
        return ""
    if isinstance(terms, dict):
        pairs = list(terms.items())
    else:
        pairs = [(t["ru"], t["zh"]) if isinstance(t, dict) else (t[0], t[1]) for t in terms]
    return "Terms (use these):\n" + "\n".join(f"{r} → {z}" for r, z in pairs) + "\n"


def marks_line(data) -> str:
    m = data.get("marks")
    return ("Known member marks (sheet titles): " + ", ".join(m) + "\n") if m else ""


def images(payload) -> list[Path]:
    return [Path(p) for p in payload.get("images") or []]
