"""rudraw command line: doctor | init | run | status | cost."""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

from . import cost, doctor as doctor_mod
from .config import JobConfig
from .core import Job
from . import runner

TEXT_MIN_CHARS = 25        # a page with at least this many extractable characters "has a text layer"


def detect_route(pdf: Path, sample: int = 12) -> dict:
    """Sample pages with PyMuPDF text extraction: text layer vs image-only scan."""
    import pymupdf
    doc = pymupdf.open(pdf)
    n = doc.page_count
    idx = sorted({int(i * (n - 1) / max(1, min(sample, n) - 1)) for i in range(min(sample, n))}) if n > 1 else [0]
    with_text, chars, cyr = 0, 0, 0
    for i in idx:
        t = doc[i].get_text() or ""
        k = len(re.sub(r"\s", "", t))
        chars += k
        cyr += len(re.findall(r"[а-яА-ЯёЁ]", t))
        with_text += k >= TEXT_MIN_CHARS
    route = "text" if with_text * 2 > len(idx) else "scan"
    return {"route": route, "pages": n, "sampled": len(idx), "pages_with_text": with_text,
            "chars": chars, "cyrillic_chars": cyr}


def _pdf_ref(folder: Path, pdf: Path) -> str:
    try:
        return pdf.resolve().relative_to(folder.resolve()).as_posix()
    except ValueError:
        return pdf.resolve().as_posix()


COMMON_TAIL = '''
[llm]
backend = "claude-cli"          # claude-cli | anthropic | fake
concurrency = 4
budget_usd = 20                 # hard stop before exceeding
background = ""                 # project background for the translator (keep private)
[llm.models]
phrase_translate = "sonnet"
page_notes = "sonnet"
audit_review = "haiku"
visual_review = "sonnet"

[tm]
project = "tm/project.json"     # written by the run
shared = []                     # read-only TMs of other volumes of the same project
glossaries = []                 # the public base glossary is added automatically

[job]
max_audit_loops = 2
visual = false                  # LLM visual review of flagged tiles (costly, optional)
'''


def skeleton(route: str, name: str, pdf_ref: str, code: str) -> str:
    if route == "scan":
        head = f'''# rudraw project (scanned PDF).  Run:  python <repo>/scripts/rudraw.py doctor / run --dry-run / run
[project]
name = "{name}"
pdf = "{pdf_ref}"

[scan]
pdf = "{pdf_ref}"
code = "{code}"                  # short tag for output names
work = "work"                    # png/ ocr/ lines/ rapid/ notes/ map/ ... and job/ (state, logs, ledger)
output = "output"
# tessdata = "tessdata"          # folder with rus.traineddata (tessdata_best) + configs/
# count_word = "根"               # 件 for embedded items / parts volumes
# title_code_regex = "^XXXX-"
# skip_regex = ""
# font = "C:/Windows/Fonts/simhei.ttf"

[keep]
names = []                       # signatures, designer logo: never translated, never audited
acronyms = []
'''
    else:
        head = f'''# rudraw project (PDF with a text layer).  Run:  python <repo>/scripts/rudraw.py doctor / run --dry-run / run
[project]
name = "{name}"
pdf = "{pdf_ref}"
tm = "dict/tm.json"
review_dir = "review"
output_dir = "output"
extract_dir = "extract"
# font = "C:/Windows/Fonts/simhei.ttf"
# notes_pages = "3,4"

[keep]
names = []
acronyms = []
'''
    return head + COMMON_TAIL


def cmd_init(a) -> int:
    folder = Path(a.folder).resolve()
    pdf = Path(a.pdf).resolve()
    if not pdf.is_file():
        print(f"error: PDF not found: {pdf}", file=sys.stderr)
        return 1
    info = detect_route(pdf)
    route = a.route if a.route != "auto" else info["route"]
    target = folder / "project.toml"
    if target.exists() and not a.force:
        print(f"error: {target} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    folder.mkdir(parents=True, exist_ok=True)
    code = a.code or re.sub(r"[^A-Za-z0-9]+", "", pdf.stem)[:8].upper() or "DOC"
    target.write_text(skeleton(route, a.name or pdf.stem, _pdf_ref(folder, pdf), code), encoding="utf-8")
    print(f"{pdf.name}: {info['pages']} page(s), {info['pages_with_text']}/{info['sampled']} sampled with a text layer, "
          f"{info['cyrillic_chars']} Cyrillic chars -> route: {route}" + ("  (forced)" if a.route != "auto" else ""))
    print(f"wrote {target}\nnext: python scripts/rudraw.py doctor --config {target}\n"
          f"      python scripts/rudraw.py run --dry-run --config {target}")
    return 0


def _job(a) -> Job:
    return Job(JobConfig.load(a.config))


def cmd_doctor(a) -> int:
    cfg = None
    try:
        cfg = JobConfig.load(a.config) if (a.config or Path("project.toml").exists()) else None
    except Exception as e:  # noqa: BLE001
        print(f"warning: cannot read config: {e}")
    return doctor_mod.doctor(cfg)


def cmd_run(a) -> int:
    job = _job(a)
    try:
        return runner.run_job(job, a.frm, a.only, a.until, a.force, a.pages, a.dry_run)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def cmd_status(a) -> int:
    job = _job(a)
    print(f"route {job.cfg.route}, job dir {job.job_dir}\n")
    print(runner.status_table(job))
    return 0


def cmd_cost(a) -> int:
    job = _job(a)
    rep = cost.write_report(job.job_dir, job.n_pages)
    print(cost.render_md(rep))
    print(f"written {job.job_dir / 'cost_report.md'} and cost_report.json")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rudraw", description="Russian drawing -> Chinese annotation pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    conf = argparse.ArgumentParser(add_help=False)
    conf.add_argument("--config", help="project.toml (default ./project.toml)")

    sub.add_parser("doctor", parents=[conf], help="check python deps, tesseract, tessdata, font, claude CLI").set_defaults(fn=cmd_doctor)

    p = sub.add_parser("init", help="create project.toml for a PDF (auto-detects text layer vs scan)")
    p.add_argument("folder", nargs="?", default=".")
    p.add_argument("--pdf", required=True)
    p.add_argument("--route", choices=["auto", "scan", "text"], default="auto")
    p.add_argument("--name"); p.add_argument("--code")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("run", parents=[conf], help="run the stages (resumable)")
    p.add_argument("--from", dest="frm", metavar="STAGE"); p.add_argument("--only", metavar="STAGE")
    p.add_argument("--until", metavar="STAGE")
    p.add_argument("--pages", help="e.g. 3,7-9 (page-aware stages only; partial builds go to *_test.pdf)")
    p.add_argument("--force", action="store_true", help="re-run selected stages even if unchanged")
    p.add_argument("--dry-run", action="store_true", help="print the plan and estimated LLM calls")
    p.set_defaults(fn=cmd_run)

    sub.add_parser("status", parents=[conf], help="stage table from work/job/state.json").set_defaults(fn=cmd_status)
    sub.add_parser("cost", parents=[conf], help="cost report from work/job/ledger.jsonl").set_defaults(fn=cmd_cost)
    return ap


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    a = build_parser().parse_args(argv)
    try:
        return int(a.fn(a) or 0)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
