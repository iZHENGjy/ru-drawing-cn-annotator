"""Sample pages first: one page of each kind is built and exported as PNG before the whole volume runs.

Kinds: ``prose`` (running text with a text layer), ``table``, ``drawing``, ``letter`` (scanned running
text: letters, protocols, scanned notes).  The classifier looks at a 40 dpi render only (no OCR, no
LLM): ink rows give text lines, long dark rows give table rules.

``rudraw run`` (full volume, more than ``MIN_PAGES`` pages) builds the samples, writes
``work/job/samples/*.png`` + ``samples.json`` and stops with exit code 3.  Look at the PNGs, then
``rudraw run --approve-samples`` runs the volume (the sample pages replay from the cache);
``--no-samples`` skips the step.
"""
from __future__ import annotations

import json
from pathlib import Path

MIN_PAGES = 6
KINDS = ("prose", "table", "drawing", "letter")


def _longest_runs(m):
    """longest run of True along axis 1 for every row of a boolean matrix"""
    import numpy as np
    best = np.zeros(m.shape[0], dtype=int); cur = np.zeros(m.shape[0], dtype=int)
    for j in range(m.shape[1]):
        cur = np.where(m[:, j], cur + 1, 0)
        best = np.maximum(best, cur)
    return best


def page_features(page, has_text: bool) -> dict:
    import numpy as np
    pix = page.get_pixmap(dpi=50, colorspace="gray")
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    dark = a < 140
    H, W = dark.shape
    hrun = _longest_runs(dark) / W
    vrun = _longest_runs(dark.T) / H
    hr = hrun > 0.3
    vr = vrun > 0.2
    rules = int((hr & ~np.roll(hr, 1)).sum())                                   # long horizontal lines
    vrules = int((vr & ~np.roll(vr, 1)).sum())                                  # long vertical lines
    rows = dark.mean(axis=1)
    text_rows = (rows > 0.01) & (rows < 0.35)
    bands = int((text_rows & ~np.roll(text_rows, 1)).sum())                     # text lines
    return {"rules": rules, "vrules": vrules, "bands": bands, "cover": round(float(text_rows.mean()), 3),
            "ink": round(float(dark.mean()), 4), "portrait": page.rect.height > page.rect.width,
            "a4": max(page.rect.width, page.rect.height) < 900, "text": has_text}


def classify(f: dict, scanned: bool = False) -> str:
    if f["rules"] >= 8 and f["vrules"] >= 4 and f["a4"]:
        return "table"
    if f["a4"] and f["bands"] >= 18 and f["cover"] >= 0.3 and f["rules"] < 8:
        return "letter" if (scanned or not f["text"]) else "prose"
    return "drawing"


def choose(pdf: Path, max_pages: int | None = None, scanned: bool = False) -> dict:
    """{kind: page number} — the first page of each kind (page 1 only if nothing else has that kind)."""
    import pymupdf
    doc = pymupdf.open(pdf)
    kinds: dict[str, int] = {}
    feats: dict[int, dict] = {}
    for i in range(doc.page_count if max_pages is None else min(doc.page_count, max_pages)):
        pg = doc[i]
        has_text = len((pg.get_text() or "").strip()) >= 25
        f = page_features(pg, has_text)
        k = classify(f, scanned)
        feats[i + 1] = dict(f, kind=k)
        if k not in kinds or kinds[k] == 1:
            kinds[k] = i + 1
    return {"pages": kinds, "features": feats}


def state_file(job) -> Path:
    return job.job_dir / "samples.json"


def load(job) -> dict:
    f = state_file(job)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save(job, d: dict):
    job.job_dir.mkdir(parents=True, exist_ok=True)
    state_file(job).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def latest_partial_pdf(job) -> Path | None:
    out = Path(job.cfg.scan.output) if job.cfg.route == "scan" else job.cfg.text_dir("output_dir", "output")
    fs = sorted(Path(out).glob("*.pdf"), key=lambda p: p.stat().st_mtime)
    test = [p for p in fs if "_test" in p.stem]
    pick = test or fs
    return pick[-1] if pick else None


def export_png(job, pages: dict, pdf: Path, dpi: int = 110) -> list[str]:
    """PNG of each sample page from the partial build (pages appear in ascending order there)."""
    import pymupdf
    d = pymupdf.open(pdf)
    order = sorted(set(pages.values()))
    sdir = job.job_dir / "samples"
    sdir.mkdir(parents=True, exist_ok=True)
    off = d.page_count - len(order)          # a contents page may precede the sample pages
    out = []
    for kind, pg in sorted(pages.items(), key=lambda kv: kv[1]):
        k = (pg - 1) if ("_test" not in pdf.stem and d.page_count >= max(order)) else order.index(pg) + max(off, 0)
        if k >= d.page_count:
            continue
        fn = sdir / f"p{pg:02d}_{kind}.png"
        d[k].get_pixmap(dpi=dpi).save(fn)
        out.append(str(fn))
    return out
