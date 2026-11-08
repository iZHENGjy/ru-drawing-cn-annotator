"""Text-layer route stages: extract -> build_dry -> export_todo -> translate[stub] -> merge_tm -> build -> issues -> report.

All steps are the existing scripts (01_extract, 20_build, 41_export_todo, 40_merge_tm, 43_issues) run in a
subprocess.  The LLM translate step is a STUB (TODO): it only reports how many keys are waiting in
review/todo_all.txt; a human or an external LLM fills <review>/batch.tsv (name configurable: [job] batch).
"""
from __future__ import annotations
import json
import re
from pathlib import Path

from .core import Stage, StageCtx, StageError


def _review(job) -> Path:
    return job.cfg.text_dir("review_dir", "review")


def _output(job) -> Path:
    return job.cfg.text_dir("output_dir", "output")


def _tm(job) -> Path:
    return job.cfg.text_dir("tm", "dict/tm.json")


def _batch(job) -> Path:
    return job.cfg._rel(job.cfg.job["batch"])


def run_extract(sc: StageCtx):
    rc, out = sc.run_script("01_extract.py", sc.config_arg(), check=True)
    m = re.search(r"pages (\d+) words (\d+) cyr (\d+) lat (\d+)", out)
    d = {"pages": int(m.group(1)), "words": int(m.group(2)), "cyr_words": int(m.group(3)), "lat_words": int(m.group(4))} if m else {}
    if d.get("words") == 0:
        raise StageError("no text layer in this PDF: it is a scan, use the scan route ([scan] table, see rudraw init)")
    return d


def _build_args(sc, dry: bool):
    a = [sc.pages or "all"] + sc.config_arg()
    if dry:
        a.append("--dry")
    else:
        a += ["--english", sc.cfg.job["english"]]
    return a


def _missing(out: str):
    m = re.search(r"missing TM keys: (\d+)", out)
    return int(m.group(1)) if m else None


def run_build_dry(sc: StageCtx):
    rc, out = sc.run_script("20_build.py", _build_args(sc, True), check=True)
    return {"missing_tm_keys": _missing(out)}


def run_export_todo(sc: StageCtx):
    sc.run_script("41_export_todo.py", sc.config_arg(), check=True)
    f = _review(sc.job) / "todo_all.txt"
    n = len(f.read_text(encoding="utf-8").splitlines()) if f.exists() and f.stat().st_size else 0
    return {"todo_items": n}


def run_translate(sc: StageCtx):
    """STUB (TODO): wire an LLM phrase_translate over review/tm_todo.json -> batch.tsv."""
    n = sc.job.metrics("export_todo").get("todo_items", 0)
    b = _batch(sc.job)
    sc.log(f"TODO: text route has no LLM translate stage yet; {n} key(s) in todo_all.txt; batch file: {b} "
           f"({'present' if b.exists() else 'absent'})")
    return {"todo_items": n, "batch_present": b.exists(), "stub": True}


def run_merge_tm(sc: StageCtx):
    b = _batch(sc.job)
    if not b.exists():
        return {"_skip": f"no batch file {b} (translate review/todo_all.txt into it, see README)"}
    rc, out = sc.run_script("40_merge_tm.py", [b] + sc.config_arg())
    if rc:
        raise StageError("40_merge_tm.py rejected the batch (nothing written); see the stage log")
    m = re.search(r"merged (\d+)", out)
    return {"merged": int(m.group(1)) if m else None}


def run_build(sc: StageCtx):
    rc, out = sc.run_script("20_build.py", _build_args(sc, False), check=True)
    return {"missing_tm_keys": _missing(out)}


def run_issues(sc: StageCtx):
    sc.run_script("43_issues.py", sc.config_arg(), check=True)
    return {}


def run_report(sc: StageCtx):
    from . import summary
    return summary.write(sc.job)


STAGES = [
    Stage("extract", "local", run_extract, "survey: words per page (01_extract.py)", cfg_keys=("project",),
          outputs=lambda j: [str(j.cfg.text_dir("extract_dir", "extract") / "*.json"), str(_review(j) / "census.csv")]),
    Stage("build_dry", "local", run_build_dry, "analyse + TM lookup, no output (20_build.py --dry)", deps=("extract",),
          cfg_keys=("project", "keep", "title_block", "segmentation"), pages_aware=True,
          inputs=lambda j: [str(_tm(j))], outputs=lambda j: [str(_review(j) / "tm_todo.json")]),
    Stage("export_todo", "local", run_export_todo, "numbered todo packet (41_export_todo.py)", deps=("build_dry",),
          outputs=lambda j: [str(_review(j) / "todo_all.txt")]),
    Stage("translate", "llm", run_translate, "[STUB/TODO] LLM translation of todo_all.txt -> batch.tsv",
          deps=("export_todo",), always=True, outputs=lambda j: [str(_batch(j))]),
    Stage("merge_tm", "tm", run_merge_tm, "validated merge into the project TM (40_merge_tm.py)", deps=("translate",),
          inputs=lambda j: [str(_batch(j))], outputs=lambda j: [str(_tm(j))]),
    Stage("build", "local", run_build, "build the annotated PDF (20_build.py)", deps=("merge_tm",),
          cfg_keys=("project", "keep", "title_block", "segmentation", "job.english"), pages_aware=True,
          outputs=lambda j: [str(_output(j) / "*.pdf"), str(_review(j) / "report.csv")]),
    Stage("issues", "local", run_issues, "reviewer issue list (43_issues.py)", deps=("build",),
          outputs=lambda j: [str(_output(j) / "问题清单.md")]),
    Stage("report", "local", run_report, "run_summary.json + cost report", always=True, deps=("issues",)),
]
