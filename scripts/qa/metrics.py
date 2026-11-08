"""Quality metrics of one output PDF (scanned route), as flat JSON for regression comparison.

    python scripts/qa/metrics.py OUT.pdf [--work WORK] [--config project.toml] [--src SRC.pdf]
                                 [--floor 6] [--json out.json]

OUT.pdf                    the bilingual output (Chinese drawn in blue by scan/engine.py)
--work WORK                work folder: build_report.tsv (leaders, unplaced) and the audit state
--config project.toml      gives work/src/count_word and enables the mark gate (50_check.py exit code)
--src SRC.pdf              original scan: enables the occlusion summary (occlusion.py)
--audit-log FILE           stdout of 30_audit.py ("remaining N"); else the count comes from
                           work/packets/*.cands.json (what 30_audit.py counted)

Metrics (key: meaning)
  pages, zh_chars          page count; CJK characters in the text layer
  n_gen, n_jian, spacing   occurrences of 根, 件, 间距 (count_label = the project's count_word)
  leaders, unplaced        '引线' / '未放置' rows of build_report.tsv
  font_min/p10/median      font size (pt) of the blue Chinese spans; below_floor = spans < floor
  audit_remaining          30_audit.py gate
  mark_gate                50_check.py exit code (0 = pass)
  text_sha256              sha256 of all page texts in order; layout_sha256 adds blue span boxes
  occ_*                    occlusion.py flag counts (tiny / ink / rule / zh_overlap / flagged)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa import common as K  # noqa: E402

FLOOR = 6.0          # pt: below this a Chinese span counts as hard to read (see occlusion.DEFAULTS)
SCAN = Path(__file__).resolve().parents[1] / "scan"


def text_fingerprints(doc):
    """(text_sha256, layout_sha256): page texts in order; and blue spans with 0.1 pt boxes."""
    h1, h2 = hashlib.sha256(), hashlib.sha256()
    for p in doc:
        h1.update(p.get_text().encode("utf-8")); h1.update(b"\f")
        for bb, size, text in K.blue_spans(p):
            h2.update(f"{p.number}|{bb[0]:.1f},{bb[1]:.1f},{bb[2]:.1f},{bb[3]:.1f}|{size:.2f}|{text}\n".encode("utf-8"))
    return h1.hexdigest(), h2.hexdigest()


def read_report(work: Path | None, report_tsv: Path | None = None):
    """-> Counter of the build_report.tsv kinds (3rd column) or None when there is no report."""
    fn = Path(report_tsv) if report_tsv else (Path(work) / "review" / "build_report.tsv" if work else None)
    if not fn or not fn.exists():
        return None
    c = Counter()
    for ln in fn.read_text(encoding="utf-8").splitlines():
        f = ln.split("\t")
        if len(f) >= 3 and not f[2].isdigit():
            c[f[2]] += 1
    return c


def audit_remaining(work: Path | None, audit_log: Path | None = None):
    if audit_log and Path(audit_log).exists():
        for ln in reversed(Path(audit_log).read_text(encoding="utf-8", errors="replace").splitlines()):
            if ln.startswith("remaining "):
                return int(ln.split()[1])
    if work and (Path(work) / "packets").exists():
        n = 0
        for f in (Path(work) / "packets").glob("p*.cands.json"):
            n += len(json.loads(f.read_text(encoding="utf-8")).get("cands", {}))
        return n
    return None


def mark_gate(config: Path | None):
    if not config:
        return None
    r = subprocess.run([sys.executable, str(SCAN / "50_check.py"), "--config", str(config)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode


def compute(out_pdf, work=None, config=None, src=None, floor=FLOOR, count_word=None, audit_log=None,
            report_tsv=None, run_check=True, dpi=150):
    """Metrics dict for one output PDF."""
    t0 = time.time()
    cfg = None
    if config:
        sys.path.insert(0, str(SCAN.parent))
        from scan import ctx as C
        cfg = C.load(config)
        work = work or cfg.work
        src = src or cfg.pdf
        count_word = count_word or cfg.count_word
    doc = K.open_pdf(out_pdf)
    text = "".join(p.get_text() for p in doc)
    sizes = np.array([s for p in doc for _, s, _ in K.blue_spans(p)]) if doc.page_count else np.array([])
    m = dict(pages=doc.page_count, zh_chars=len(K.CJK.findall(text)),
             n_gen=text.count("根"), n_jian=text.count("件"), spacing=text.count("间距"))
    m["count_label"] = text.count(count_word) if count_word else None
    rep = read_report(work, report_tsv)
    m["leaders"] = None if rep is None else rep.get("引线", 0)
    m["unplaced"] = None if rep is None else rep.get("未放置", 0)
    m["blue_spans"] = int(sizes.size)
    if sizes.size:
        m["font_min"] = round(float(sizes.min()), 2)
        m["font_p10"] = round(float(np.percentile(sizes, 10)), 2)
        m["font_median"] = round(float(np.median(sizes)), 2)
        m["below_floor"] = int((sizes < floor).sum())
    else:
        m.update(font_min=None, font_p10=None, font_median=None, below_floor=0)
    m["floor"] = floor
    m["audit_remaining"] = audit_remaining(work, audit_log)
    m["mark_gate"] = mark_gate(config) if (run_check and config) else None
    m["text_sha256"], m["layout_sha256"] = text_fingerprints(doc)
    if src:
        from qa import occlusion as O
        rows, fl = O.run(out_pdf, src, dpi, floor=floor)
        for k, v in O.summary(rows, fl).items():
            m["occ_" + k] = v
        m["occ_tiles"] = len(O.tiles(fl))
    m["seconds"] = round(time.time() - t0, 1)
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_pdf")
    ap.add_argument("--work"); ap.add_argument("--config"); ap.add_argument("--src")
    ap.add_argument("--floor", type=float, default=FLOOR)
    ap.add_argument("--count-word", help="根 or 件 (default: from --config)")
    ap.add_argument("--audit-log"); ap.add_argument("--report-tsv")
    ap.add_argument("--no-check", action="store_true", help="skip the 50_check.py call")
    ap.add_argument("--json")
    a = ap.parse_args()
    m = compute(a.out_pdf, a.work, a.config, a.src, a.floor, a.count_word, a.audit_log, a.report_tsv, not a.no_check)
    for k, v in m.items():
        print(f"{k:16s} {v}")
    if a.json:
        Path(a.json).write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
