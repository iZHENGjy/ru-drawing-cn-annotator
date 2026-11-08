"""Stage / Job primitives shared by both routes."""
from __future__ import annotations
import importlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import JobConfig
from .state import State, digest, fingerprint

SCRIPTS = Path(__file__).resolve().parents[1]


class StageError(Exception):
    """The stage failed; the run stops here (state keeps the error)."""


class StageUnavailable(StageError):
    """A module another engineer is still writing is not importable yet."""


def lazy(module: str, stage: str = ""):
    """Import a pipeline module on first use; a missing *module itself* -> StageUnavailable."""
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as e:
        if e.name and (module == e.name or module.startswith(e.name + ".")):
            raise StageUnavailable(f"stage {stage or '?'} not available yet: module '{module}' is missing") from e
        raise


def parse_pages(s: str | None, n: int) -> list[int]:
    if not s:
        return list(range(1, n + 1))
    out: list[int] = []
    for part in str(s).split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return sorted({p for p in out if 1 <= p <= n}) if n else sorted(set(out))


@dataclass
class Stage:
    name: str
    kind: str                                  # local | llm | tm
    run: Callable                              # run(sc: StageCtx) -> metrics dict
    desc: str = ""
    deps: tuple = ()                           # upstream stage names (their output fingerprints feed our hash)
    cfg_keys: tuple = ()                       # dotted config keys that influence this stage
    inputs: Callable = lambda job: []          # extra input globs (relative to project root)
    outputs: Callable = lambda job: []         # output globs
    pages_aware: bool = False
    optional: bool = False                     # missing module / environment -> skipped, run continues
    always: bool = False                       # never skipped as up-to-date
    settle: bool = False                       # stage re-runs others internally: re-store hashes afterwards
    est: Callable | None = None                # est(job) -> (calls:int, note:str)
    gate_keys: tuple = ()                      # metrics keys that are delivery gates (informational)


class Job:
    def __init__(self, cfg: JobConfig, out: Callable = print):
        self.cfg = cfg
        self.out = out
        self.job_dir = cfg.job_dir
        self.log_dir = self.job_dir / "logs"
        self.state = State(self.job_dir / "state.json")
        self._ctx = None
        self._n = None
        self.pages_arg: str | None = None
        from . import stages_scan, stages_text
        self.stages: list[Stage] = stages_scan.STAGES if cfg.route == "scan" else stages_text.STAGES

    # ---- helpers
    @property
    def root(self) -> Path:
        return self.cfg.root

    @property
    def ctx(self):
        if self._ctx is None:
            from scan import ctx as C
            self._ctx = C.Ctx(self.cfg.scan)
        return self._ctx

    @property
    def n_pages(self) -> int:
        if self._n is None:
            self._n = 0
            pdf = self.cfg.pdf
            if pdf and Path(pdf).is_file():
                try:
                    import pymupdf
                    self._n = pymupdf.open(pdf).page_count
                except Exception:
                    pass
            if not self._n:
                self._n = int(self.state.data.get("pages") or 0)
        return self._n

    def stage(self, name: str) -> Stage:
        for s in self.stages:
            if s.name == name:
                return s
        raise KeyError(name)

    def names(self) -> list[str]:
        return [s.name for s in self.stages]

    def metrics(self, name: str) -> dict:
        return self.state.get(name).get("metrics") or {}

    # ---- hashing
    def outputs_fp(self, st: Stage) -> str:
        return fingerprint(self.root, st.outputs(self))

    def inputs_hash(self, st: Stage, pages: str | None) -> str:
        pdf = self.cfg.pdf
        return digest({
            "cfg": {k: self.cfg.section(k) for k in st.cfg_keys},
            "pdf": fingerprint(self.root, [str(pdf)]) if pdf and st.name in ("ocr", "extract") else "",
            "inputs": fingerprint(self.root, st.inputs(self)),
            "deps": {d: self.state.get(d).get("outputs_fp", "") for d in st.deps},
            "pages": (pages or "") if st.pages_aware else "",
        })

    def settle(self, name: str):
        """Re-store hashes of a done stage after another stage changed its files on purpose."""
        st = self.stage(name)
        rec = self.state.get(name)
        if rec.get("status") == "done":
            self.state.set(name, inputs_hash=self.inputs_hash(st, rec.get("pages")), outputs_fp=self.outputs_fp(st))

    def up_to_date(self, st: Stage, pages: str | None) -> bool:
        rec = self.state.get(st.name)
        return (rec.get("status") == "done" and not st.always
                and rec.get("inputs_hash") == self.inputs_hash(st, pages)
                and rec.get("outputs_fp") == self.outputs_fp(st))


class StageCtx:
    """What a stage function gets: job, page selection, a log, subprocess helper."""

    def __init__(self, job: Job, stage: Stage, pages: str | None, log_file, echo: bool = True):
        self.job, self.stage, self.pages, self._f, self.echo = job, stage, pages, log_file, echo

    @property
    def cfg(self) -> JobConfig:
        return self.job.cfg

    def page_list(self) -> list[int]:
        return parse_pages(self.pages, self.job.n_pages)

    def log(self, msg: str, echo: bool | None = None):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self._f.write(line + "\n"); self._f.flush()
        if self.echo if echo is None else echo:
            self.job.out(f"    {msg}")

    def run_script(self, rel: str, args=(), check: bool = False) -> tuple[int, str]:
        """Run scripts/<rel> with this python; stream output into the stage log; return (rc, text)."""
        cmd = [sys.executable, str(SCRIPTS / rel), *map(str, args)]
        self.log("$ " + " ".join(cmd[1:]), echo=False)
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        p = subprocess.Popen(cmd, cwd=self.job.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        lines = []
        for ln in p.stdout:
            ln = ln.rstrip("\r\n")
            lines.append(ln)
            self.log(ln, echo=self.echo and len(lines) <= 40)
        rc = p.wait()
        self.log(f"exit code {rc}", echo=False)
        if check and rc:
            raise StageError(f"{rel} exited with code {rc} (see {self.job.log_dir / (self.stage.name + '.log')})")
        return rc, "\n".join(lines)

    def config_arg(self) -> list[str]:
        return ["--config", str(self.cfg.path)]

    def pages_args(self) -> list[str]:
        return ["--pages", self.pages] if self.pages else []

    def session(self, stage_name: str | None = None):
        """LLM Session (scripts/llm, written by another engineer)."""
        mod = lazy("llm", self.stage.name)
        return mod.Session(Path(self.cfg.work_dir), self.cfg.llm, stage_name or self.stage.name)


def summarize_results(results) -> dict:
    ok = sum(1 for r in results if r.ok)
    return {"calls": len(results), "ok": ok, "failed": len(results) - ok,
            "cached": sum(1 for r in results if getattr(r, "cached", False)),
            "cost_usd": round(sum(float(getattr(r, "cost_usd", 0) or 0) for r in results), 4)}


def fail_on_bad(results, what: str):
    bad = [r for r in results if not r.ok]
    if bad:
        errs = "; ".join(f"{r.id}: {'|'.join(r.errors)[:120]}" for r in bad[:3])
        raise StageError(f"{what}: {len(bad)}/{len(results)} LLM item(s) failed ({errs}); re-run to retry only those "
                         f"(successful calls replay from the cache for free)")
