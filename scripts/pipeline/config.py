"""Job configuration: wraps scan/ctx.py's ScanConfig and adds the [llm] / [tm] / [job] tables."""
from __future__ import annotations
import tomllib
from copy import deepcopy
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_GLOSSARY = REPO / "glossary" / "gost_general.json"
DEFAULT_GLOSSARIES = [DEFAULT_GLOSSARY, REPO / "glossary" / "spds_sheet_frame.json"]   # public base layers

LLM_DEFAULTS = {
    "backend": "claude-cli", "concurrency": 4, "budget_usd": 20, "background": "",
    "models": {"phrase_translate": "sonnet", "page_notes": "sonnet",
               "audit_review": "haiku", "visual_review": "sonnet"},
}
TM_DEFAULTS = {"project": "tm/project.json", "shared": [], "glossaries": []}
JOB_DEFAULTS = {
    "route": "auto",          # auto | scan | text
    "max_audit_loops": 2,     # audit_review -> apply -> rebuild -> audit, at most this many times
    "visual": False,          # run the visual_review LLM stage (flagged tiles only)
    "qa": True,               # run scripts/qa when it exists
    "batch_size": 60,         # phrase_translate items per LLM call
    "english": "keep",        # text route: --english keep|replace
    "batch": "review/batch.tsv",   # text route: translated batch for 40_merge_tm.py
}


def _merge(base: dict, over: dict) -> dict:
    out = deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class JobConfig:
    def __init__(self, path: Path, raw: dict):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        self.raw = raw
        self.llm = _merge(LLM_DEFAULTS, raw.get("llm", {}))
        self.tm = _merge(TM_DEFAULTS, raw.get("tm", {}))
        self.job = _merge(JOB_DEFAULTS, raw.get("job", {}))
        self.route = self.job["route"] if self.job["route"] != "auto" else ("scan" if "scan" in raw else "text")
        self._scan = None

    @classmethod
    def load(cls, path=None) -> "JobConfig":
        p = Path(path or "project.toml").resolve()
        if not p.exists():
            raise FileNotFoundError(f"config not found: {p} (create one with: rudraw init <folder> --pdf file.pdf)")
        return cls(p, tomllib.loads(p.read_text(encoding="utf-8")))

    # ---- scan route (ScanConfig untouched)
    @property
    def scan(self):
        if self._scan is None:
            from scan import ctx as C
            self._scan = C.load(self.path)
        return self._scan

    def _rel(self, v) -> Path:
        p = Path(str(v).replace("<repo>", str(REPO)))
        return p if p.is_absolute() else self.root / p

    @property
    def pdf(self) -> Path | None:
        if self.route == "scan":
            return self.scan.pdf
        v = self.raw.get("project", {}).get("pdf")
        return self._rel(v) if v else None

    @property
    def work_dir(self) -> Path:
        """Folder holding job/, llm/ and (scan route) png/ ocr/ ... (same as [scan] work)."""
        return Path(self.scan.work) if self.route == "scan" else self.root / "work"

    @property
    def job_dir(self) -> Path:
        return self.work_dir / "job"

    def tm_cfg(self) -> dict:
        gl = [self._rel(g) for g in self.tm["glossaries"]]
        for i, dg in enumerate(g for g in DEFAULT_GLOSSARIES if g.exists()):
            if dg not in gl:
                gl.insert(i, dg)
        return {"project": str(self._rel(self.tm["project"])), "shared": [str(self._rel(s)) for s in self.tm["shared"]],
                "glossaries": [str(g) for g in gl], "root": str(self.root)}

    def section(self, dotted: str):
        """raw config value by dotted key ('scan', 'llm.models') for input hashing."""
        cur = {**self.raw, "llm": self.llm, "tm": self.tm, "job": self.job}
        for part in dotted.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    # ---- text route folders (same defaults as engine/config.py)
    def text_dir(self, key: str, default: str) -> Path:
        return self._rel(self.raw.get("project", {}).get(key, default))
