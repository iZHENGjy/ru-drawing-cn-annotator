"""Task registry.  Each task module defines:

    NAME, PROMPT_VERSION (part of the cache key), SCHEMA (JSON Schema of the answer),
    render(payload) -> (text, images), validate(payload, output) -> list[str],
    fake(payload) -> dict (schema-valid conservative answer for the fake backend),
    optional retry_payload(payload, output, errors) -> payload | None and merge(prev, new) -> dict
    (retry only the failing items of a batch).

The instructions live next to the module as ``<name>.md`` (English instructions, Chinese
output, generic).  The project background is appended at run time from cfg["background"].
"""
from __future__ import annotations

import hashlib
import importlib
from pathlib import Path

NAMES = ("phrase_translate", "page_notes", "audit_review", "visual_review")
_HERE = Path(__file__).resolve().parent
_cache = {}


class Task:
    def __init__(self, mod):
        self.mod = mod
        self.name = mod.NAME
        self.prompt_version = mod.PROMPT_VERSION
        self.schema = mod.SCHEMA
        self.prompt = (_HERE / f"{self.name}.md").read_text(encoding="utf-8").strip()

    def system(self, background: str = "") -> str:
        s = self.prompt
        if background and background.strip():
            s += "\n\n## Project background (context only)\n" + background.strip()
        return s

    def render(self, payload):
        return self.mod.render(payload)

    def validate(self, payload, output) -> list[str]:
        try:
            return self.mod.validate(payload, output)
        except Exception as e:  # a crashing validator must never accept an answer
            return [f"validator error: {type(e).__name__}: {e}"]

    def fake(self, payload):
        return self.mod.fake(payload)

    def retry_payload(self, payload, output, errors):
        f = getattr(self.mod, "retry_payload", None)
        return f(payload, output, errors) if f else None

    def merge(self, prev, new):
        f = getattr(self.mod, "merge", None)
        return f(prev, new) if f and prev is not None else new

    def system_hash(self, background: str = "") -> str:
        return hashlib.sha256(self.system(background).encode("utf-8")).hexdigest()[:16]


def get(name: str) -> Task:
    if name not in NAMES:
        raise KeyError(f"unknown LLM task {name!r}; known: {', '.join(NAMES)}")
    if name not in _cache:
        _cache[name] = Task(importlib.import_module(f".{name}", __name__))
    return _cache[name]
