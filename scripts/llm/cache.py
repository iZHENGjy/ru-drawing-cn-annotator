"""Response cache (replay).  work/llm/cache/<sha256>.json, written atomically.

key = sha256 of [task, prompt_version, model, data, image sha256s, system-prompt hash]; the
system-prompt hash covers edits of the prompt file and the injected project background.
Only validated answers are stored.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def file_hash(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def make_key(task: str, prompt_version: str, model: str, data, images, sys_hash: str) -> str:
    blob = json.dumps([task, prompt_version, model, data, [file_hash(p) for p in images], sys_hash],
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Cache:
    def __init__(self, root: Path):
        self.root = Path(root)

    def path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> dict | None:
        p = self.path(key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, key: str, entry: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=key[:12], suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(entry, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path(key))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
