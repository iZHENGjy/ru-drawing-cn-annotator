"""work/job/state.json: per-stage status + input hash; fingerprints of files."""
from __future__ import annotations
import hashlib
import json
import os
import time
from pathlib import Path


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def fingerprint(root: Path, patterns) -> str:
    """Hash of (relative path, size, mtime_ns) of every file matching the glob patterns."""
    root = Path(root)
    items = []
    for pat in patterns:
        pp = Path(pat)
        if pp.is_absolute():
            files = [pp] if pp.is_file() else (list(pp.parent.glob(pp.name)) if pp.parent.exists() else [])
        else:
            files = list(root.glob(pat))
        for f in files:
            if f.is_file():
                st = f.stat()
                try:
                    rel = f.relative_to(root).as_posix()
                except ValueError:
                    rel = f.as_posix()
                items.append((rel, st.st_size, st.st_mtime_ns))
    items.sort()
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()[:16]


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {"version": 1, "stages": {}}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                self.data.setdefault("stages", {})
            except (OSError, ValueError):
                pass

    def get(self, stage: str) -> dict:
        return self.data["stages"].get(stage, {})

    def set(self, stage: str, **kw):
        self.data["stages"].setdefault(stage, {}).update(kw)
        self.save()

    def reset(self, stage: str):
        self.data["stages"].pop(stage, None)
        self.save()

    def meta(self, **kw):
        self.data.update(kw)
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
