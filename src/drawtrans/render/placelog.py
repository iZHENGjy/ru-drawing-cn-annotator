"""Record of every target-language text the engines place (sidecar JSON next to the output PDF).

The PDF is never touched by this module.  ``qa/standard.py`` reads the record to check the rules
of docs/output_standard.md (not above the source, whole paragraphs, title block in one legend line,
table rows numbered, font floor).
"""
from __future__ import annotations

import json
from pathlib import Path

RECORDS: list[dict] = []


def add(**kw):
    RECORDS.append({k: (list(map(lambda v: round(float(v), 2), x)) if isinstance(x, (tuple, list)) and x and
                        isinstance(x[0], (int, float)) else (round(x, 2) if isinstance(x, float) else x))
                    for k, x in kw.items()})


def dump(path, meta: dict | None = None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"meta": meta or {}, "items": RECORDS}, ensure_ascii=False, indent=0), encoding="utf-8")
    return path


def reset():
    RECORDS.clear()
