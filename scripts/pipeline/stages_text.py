"""Text-layer route stages: extract -> build_dry -> export_todo -> translate -> merge_tm -> build -> issues -> report.

The local steps are the existing scripts (01_extract, 20_build, 41_export_todo, 40_merge_tm, 43_issues) run in a
subprocess.  translate runs the LLM task phrase_translate over <review>/tm_todo.json (keys with numbers as ⟨n⟩)
and writes the validated answers to <review>/batch.tsv ([job] batch), which merge_tm merges into the TM.
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


def _guard(key: str) -> str:
    """Start of the key for the batch.tsv guard column (no tabs, stops before the first placeholder)."""
    return key.replace("\t", " ").split("⟨")[0][:24]


def run_translate(sc: StageCtx):
    """phrase_translate over review/tm_todo.json -> batch.tsv (validated; merged by the merge_tm stage).

    The keys already carry numbers as ⟨n⟩.  Only answers that pass TM.check_entry are written, so
    40_merge_tm.py never rejects the batch; a re-run replays finished calls from the cache for free."""
    todo_f = _review(sc.job) / "tm_todo.json"
    b = _batch(sc.job)
    todo = json.loads(todo_f.read_text(encoding="utf-8")) if todo_f.exists() else {}
    if not todo:
        b.parent.mkdir(parents=True, exist_ok=True)
        b.write_text("", encoding="utf-8")
        return {"todo_items": 0, "calls": 0}
    from engine.tm import TM
    from .core import summarize_results, lazy
    keys = list(todo)
    items = [{"id": str(i), "key": k, "sample": (v or {}).get("ru", k) if isinstance(v, dict) else k,
              "n": len((v or {}).get("pages") or [1]) if isinstance(v, dict) else 1} for i, (k, v) in enumerate(todo.items())]
    size = int(sc.cfg.job.get("batch_size", 60))
    payloads, ids = [], []
    for i in range(0, len(items), size):
        payloads.append({"data": {"items": items[i:i + size], "terms": []}, "images": []})
        ids.append(f"tx{i // size + 1:03d}")
    sess = sc.session()
    results = sess.run("phrase_translate", payloads, ids)
    vmod = lazy("llm.validate", "translate")
    answers = {}
    for r in results:
        out = r.output if isinstance(r.output, dict) else None
        if out is None:
            continue
        bad = set() if r.ok else (vmod.bad_ids(r.errors) or {a.get("id") for a in out.get("items") or []})
        for a in out.get("items") or []:
            if str(a.get("id")) not in bad:
                answers[str(a.get("id"))] = a
    rows, rejected = [], []
    for i, k in enumerate(keys):
        a = answers.get(str(i))
        if a is None:
            continue
        zh = (a.get("zh") or "").strip() if a.get("action", "translate") == "translate" else ""
        e = TM.check_entry(k, zh) if zh else None
        if e:
            rejected.append(f"{i}: {e}")
            continue
        rows.append(f"{i}\t{_guard(k)}\t{zh.replace(chr(9), ' ').replace(chr(10), ' ') or '∅'}")
    b.parent.mkdir(parents=True, exist_ok=True)
    b.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    for e in rejected[:20]:
        sc.log("rejected: " + e)
    left = len(keys) - len(rows)
    m = {"todo_items": len(keys), "answered": len(rows), "unresolved": left, **summarize_results(results)}
    sc.log(f"translate: {m}")
    if left > 0.1 * len(keys):
        raise StageError(f"{left} of {len(keys)} keys unresolved after retries; re-run translate")
    return m


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
    Stage("translate", "llm", run_translate, "LLM phrase_translate of tm_todo.json -> batch.tsv",
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
