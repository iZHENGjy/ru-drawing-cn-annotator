"""Determinism check: build twice (two fresh processes, separate output folders) and compare.

    python scripts/qa/determinism.py --config project.toml [--pages 1-5] [--keep DIR]

The work folder is only read (build_report goes to the output folders).  Reports identical /
different for the text-layer fingerprint, the blue-span layout fingerprint and every metric.
Exit 0 when identical, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa import common as K, metrics as M  # noqa: E402

SCAN = Path(__file__).resolve().parents[1] / "scan"


def _load_build():
    import importlib.util
    spec = importlib.util.spec_from_file_location("scan_build", SCAN / "20_build.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def child(config, out_dir, pages):
    """Build into out_dir (own process); writes out.pdf + report.tsv.  Never touches cfg.output / work."""
    sys.path.insert(0, str(SCAN.parent))
    from scan import ctx as C, engine
    import pymupdf
    B = _load_build()
    cfg = C.load(config)
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    cfg.output = out_dir
    ctx = C.Ctx(cfg); engine.init(ctx)
    n = pymupdf.open(cfg.pdf).page_count
    sel = K.parse_pages(pages, n)
    body = out_dir / "body.pdf"
    B.build(ctx, sel, body, out_dir / "report.tsv", verbose=False)
    if len(sel) == n:
        B.add_toc(ctx, body, out_dir / "out.pdf")
    else:
        body.replace(out_dir / "out.pdf")


def run_twice(config, pages=None, keep=None):
    base = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="rudraw_det_"))
    res = []
    for tag in ("A", "B"):
        d = base / tag
        t0 = time.time()
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_child", "--config", str(config),
                            "--out", str(d)] + (["--pages", pages] if pages else []),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            raise RuntimeError(f"build {tag} failed:\n{r.stderr[-1500:]}")
        m = M.compute(d / "out.pdf", config=None, report_tsv=d / "report.tsv", work=None)
        m["build_seconds"] = round(time.time() - t0, 1)
        res.append(m)
    return res, base


def diff(a: dict, b: dict):
    skip = {"seconds", "build_seconds"}
    return {k: (a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if k not in skip and a.get(k) != b.get(k)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True); ap.add_argument("--pages"); ap.add_argument("--keep")
    ap.add_argument("--json"); ap.add_argument("--out"); ap.add_argument("--_child", action="store_true")
    a = ap.parse_args()
    if a._child:
        child(a.config, a.out, a.pages)
        return
    (A, B), base = run_twice(a.config, a.pages, a.keep)
    d = diff(A, B)
    print(f"text layer : {'identical' if A['text_sha256'] == B['text_sha256'] else 'DIFFERENT'}  {A['text_sha256'][:16]}")
    print(f"layout     : {'identical' if A['layout_sha256'] == B['layout_sha256'] else 'DIFFERENT'}  {A['layout_sha256'][:16]}")
    print("metrics    : " + ("identical" if not d else f"DIFFERENT {d}"))
    print(f"build time : {A['build_seconds']}s / {B['build_seconds']}s   (outputs: {base})")
    if a.json:
        Path(a.json).write_text(json.dumps(dict(A=A, B=B, diff=d), ensure_ascii=False, indent=1), encoding="utf-8")
    sys.exit(1 if d else 0)


if __name__ == "__main__":
    main()
