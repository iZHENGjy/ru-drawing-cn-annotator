"""Automatic 「遮挡」 detector: which blue Chinese spans sit on original ink, cross a table rule,
are too small to read, or overlap other Chinese.

    python scripts/qa/occlusion.py OUT.pdf --src SRC.pdf [--dpi 150] [--json flagged.json]
        [--todo _todo.tsv --code DOC]       # score the detector against reviewer findings

How: the output page and the ORIGINAL page (same index minus the contents pages) are rendered
at the same dpi.  Pixels that differ and are blue are the Chinese glyph pixels; they are tested
against the original's text ink and against its long horizontal/vertical rules.  Flags:

    tiny        font size < floor
    ink         share of the glyph hull covered by original text ink >= ink_box
                (or share of glyph pixels lying on ink >= ink_glyph)
    rule        a table rule passes through the glyph rows/columns (not merely touching the edge)
    zh_overlap  bbox overlaps another Chinese span

The flagged boxes are merged into tiles (page, bbox) for the visual-review stage.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa import common as K  # noqa: E402

# calibrated on reviewer findings (round 2) of two real sets, see docs / QA.md
DEFAULTS = dict(floor=6.0, ink_box=0.03, ink_glyph=0.15, rule_frac=0.5, dpi=150)


def _arr(pix, ch):
    a = np.frombuffer(pix.samples, np.uint8)
    return a.reshape(pix.height, pix.width) if ch == 1 else a.reshape(pix.height, pix.width, ch)


def page_masks(src_page, out_page, dpi):
    """(text_ink, rules, blue) boolean masks, all on the OUTPUT page's pixel grid."""
    s = dpi / 72.0
    mat = pymupdf.Matrix(s, s)
    gray = _arr(src_page.get_pixmap(matrix=mat, colorspace=pymupdf.csGRAY), 1)
    L = max(8, int(round(15 * s)))
    rules = K.dilate(K.runs_mask(gray < 200, L, 1) | K.runs_mask(gray < 200, L, 0), 1)
    text_ink = (gray < 170) & ~rules
    o = _arr(out_page.get_pixmap(matrix=mat, colorspace=pymupdf.csRGB), 3).astype(np.int16)
    r0 = _arr(src_page.get_pixmap(matrix=mat, colorspace=pymupdf.csRGB), 3).astype(np.int16)
    H, W = min(o.shape[0], r0.shape[0]), min(o.shape[1], r0.shape[1])
    bluish = o[..., 2] > o[..., 0] + 20
    blue = np.zeros(o.shape[:2], bool)
    blue[:H, :W] = (np.abs(o[:H, :W] - r0[:H, :W]).max(axis=2) > 40) & bluish[:H, :W]
    if o.shape[1] > W or o.shape[0] > H:                 # widened page: right margin is overlay on white
        extra = bluish & (o.min(axis=2) < 215)
        extra[:H, :W] = False
        blue |= extra
    ti = np.zeros(o.shape[:2], bool); ru = np.zeros(o.shape[:2], bool)
    h, w = min(H, gray.shape[0]), min(W, gray.shape[1])
    ti[:h, :w] = text_ink[:h, :w]; ru[:h, :w] = rules[:h, :w]
    return ti, ru, blue


def span_features(box, ti, ru, blue, s):
    x0, y0 = max(0, int(np.floor(box[0] * s))), max(0, int(np.floor(box[1] * s)))
    x1, y1 = min(blue.shape[1], int(np.ceil(box[2] * s))), min(blue.shape[0], int(np.ceil(box[3] * s)))
    f = dict(ink_box=0.0, ink_glyph=0.0, rule_h=0.0, rule_v=0.0, n_glyph=0)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return f
    gl = blue[y0:y1, x0:x1]
    n = int(gl.sum())
    f["n_glyph"] = n
    if n < 6:
        return f
    ys, xs = np.nonzero(gl)
    hull = (slice(y0 + ys.min(), y0 + ys.max() + 1), slice(x0 + xs.min(), x0 + xs.max() + 1))   # tight glyph hull
    f["ink_box"] = float(ti[hull].mean())
    f["ink_glyph"] = float((ti[y0:y1, x0:x1] & gl).sum() / n)
    rr = ru[hull]
    hh, ww = rr.shape
    m = max(2, int(round(0.12 * hh))); mx = max(2, int(round(0.04 * ww)))
    if hh - 2 * m > 0:                                    # a rule row strictly inside the glyph rows
        f["rule_h"] = float(rr.mean(axis=1)[m:hh - m].max())
    if ww - 2 * mx > 0:
        f["rule_v"] = float(rr.mean(axis=0)[mx:ww - mx].max())
    return f


def analyse(out_pdf, src_pdf, dpi=DEFAULTS["dpi"], pages=None, progress=False):
    """Per-span features for every blue span of the drawing pages -> list of dicts."""
    od, sd = K.open_pdf(out_pdf), K.open_pdf(src_pdf)
    off = K.page_offset(od, sd)
    s = dpi / 72.0
    rows = []
    for sp in (pages or range(1, sd.page_count + 1)):
        op = od[sp - 1 + off]
        spans = K.blue_spans(op)
        if not spans:
            continue
        t0 = time.time()
        ti, ru, bl = page_masks(sd[sp - 1], op, dpi)
        base = len(rows)
        for i, (bb, size, text) in enumerate(spans):
            f = span_features(bb, ti, ru, bl, s)
            f.update(page=sp + off, src_page=sp, bbox=[round(v, 1) for v in bb], size=round(size, 2), text=text)
            rows.append(f)
        for i in range(len(spans)):                       # Chinese on Chinese
            for j in range(i + 1, len(spans)):
                if K.box_overlap(spans[i][0], spans[j][0]) > 0.3:
                    rows[base + i]["zh_overlap"] = rows[base + j]["zh_overlap"] = True
        if progress:
            print(f"  page {sp}: {len(spans)} spans ({time.time() - t0:.1f}s)", file=sys.stderr, flush=True)
    return rows


def flag(rows, floor=DEFAULTS["floor"], ink_box=DEFAULTS["ink_box"], ink_glyph=DEFAULTS["ink_glyph"],
         rule_frac=DEFAULTS["rule_frac"]):
    """Set ``flags`` on every row; return only the flagged rows."""
    out = []
    for r in rows:
        fl = []
        if r["size"] < floor:
            fl.append("tiny")
        if r["ink_box"] >= ink_box or r["ink_glyph"] >= ink_glyph:
            fl.append("ink")
        if r["rule_h"] >= rule_frac or r["rule_v"] >= rule_frac:
            fl.append("rule")
        if r.get("zh_overlap"):
            fl.append("zh_overlap")
        r["flags"] = fl
        if fl:
            out.append(r)
    return out


def tiles(flagged, pad=6.0, merge_gap=24.0):
    """Merge flagged boxes (per page) into review tiles [{page, src_page, bbox, flags, n}]."""
    out = []
    for pg in sorted({r["page"] for r in flagged}):
        boxes = [[r["bbox"][0] - pad, r["bbox"][1] - pad, r["bbox"][2] + pad, r["bbox"][3] + pad,
                  set(r["flags"]), 1, r["src_page"]] for r in flagged if r["page"] == pg]
        again = True
        while again:
            again = False
            for i in range(len(boxes)):
                for j in range(i + 1, len(boxes)):
                    a, b = boxes[i], boxes[j]
                    if a[0] - merge_gap <= b[2] and b[0] - merge_gap <= a[2] and a[1] - merge_gap <= b[3] and b[1] - merge_gap <= a[3]:
                        boxes[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]), a[4] | b[4], a[5] + b[5], a[6]]
                        del boxes[j]
                        again = True
                        break
                if again:
                    break
        for b in boxes:
            out.append(dict(page=pg, src_page=b[6], bbox=[round(max(0, v), 1) for v in b[:4]], flags=sorted(b[4]), n=b[5]))
    return out


def summary(rows, flagged):
    c = {k: sum(1 for r in flagged if k in r["flags"]) for k in ("tiny", "ink", "rule", "zh_overlap")}
    return dict(spans=len(rows), flagged=len(flagged), **c)


# ---------------------------------------------------------------- scoring against reviewer findings
def load_todo(tsv, code, kinds=("遮挡", "数量标注遮挡")):
    """[(src_page, (x0,y0,x1,y1), note)] of one set's findings (TSV: id, kind, note, ?, [box])."""
    out = []
    with open(tsv, encoding="utf-8", newline="") as fh:
        for r in csv.reader(fh, delimiter="\t"):
            if len(r) < 5 or not r[0].startswith(code + "_p") or r[1] not in kinds:
                continue
            try:
                box = tuple(float(v) for v in json.loads(r[4]))
                if len(box) == 4:
                    out.append((int(r[0].split("_p")[1]), box, r[2]))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
    return out


def score(flagged, todo, min_ov=0.3):
    """Detector vs findings.  recall = findings overlapped by a flagged span; precision = flagged
    spans overlapping some finding (reviewers did not list every defect: a lower bound)."""
    def hit(r, pg, box):
        return r["src_page"] == pg and K.box_overlap(r["bbox"], box) >= min_ov
    found = [any(hit(r, pg, box) for r in flagged) for pg, box, _ in todo]
    tp = sum(1 for r in flagged if any(hit(r, pg, box) for pg, box, _ in todo))
    return dict(findings=len(todo), recall=sum(found) / max(len(todo), 1), flagged=len(flagged),
                flagged_on_finding=tp, precision=tp / max(len(flagged), 1),
                missed=[(pg, box, note) for (pg, box, note), ok in zip(todo, found) if not ok])


def run(out_pdf, src_pdf, dpi=DEFAULTS["dpi"], pages=None, progress=False, **thr):
    rows = analyse(out_pdf, src_pdf, dpi, pages, progress)
    fl = flag(rows, **thr)
    return rows, fl


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_pdf"); ap.add_argument("--src", required=True, help="original (un-annotated) PDF")
    ap.add_argument("--dpi", type=int, default=DEFAULTS["dpi"])
    ap.add_argument("--floor", type=float, default=DEFAULTS["floor"])
    ap.add_argument("--ink-box", type=float, default=DEFAULTS["ink_box"])
    ap.add_argument("--ink-glyph", type=float, default=DEFAULTS["ink_glyph"])
    ap.add_argument("--pages", help="source page numbers, e.g. 1-5,9")
    ap.add_argument("--json", help="write flagged spans + tiles here")
    ap.add_argument("--todo", help="reviewer findings TSV (private) to score against"); ap.add_argument("--code")
    a = ap.parse_args()
    sd = K.open_pdf(a.src)
    t0 = time.time()
    rows, fl = run(a.out_pdf, sd, a.dpi, K.parse_pages(a.pages, sd.page_count), True,
                   floor=a.floor, ink_box=a.ink_box, ink_glyph=a.ink_glyph)
    tl = tiles(fl)
    res = dict(summary=summary(rows, fl), tiles=tl, flagged=fl, seconds=round(time.time() - t0, 1))
    print(json.dumps(res["summary"]), f"tiles={len(tl)}", f"{res['seconds']}s")
    if a.todo:
        sc = score(fl, load_todo(a.todo, a.code or ""))
        res["score"] = {k: v for k, v in sc.items() if k != "missed"}
        print(f"vs findings: n={sc['findings']} recall={sc['recall']:.2f} precision={sc['precision']:.2f}")
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
