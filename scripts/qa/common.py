"""Shared helpers: blue-span extraction, page-offset detection, tiny image-mask utilities."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pymupdf

# scan/engine.py BLUE = (0x00, 0x2E, 0xBF): colour of every Chinese span the engine draws
BLUE_RGB = (0x00, 0x2E, 0xBF)
CJK = re.compile(r"[\u4e00-\u9fff]")


def is_blue(color: int, tol: int = 4) -> bool:
    r, g, b = (color >> 16) & 255, (color >> 8) & 255, color & 255
    return all(abs(a - e) <= tol for a, e in zip((r, g, b), BLUE_RGB))


def blue_spans(page):
    """[(bbox, size, text)] of the blue (engine-drawn) text spans on one page, visual orientation."""
    out = []
    for b in page.get_text("dict").get("blocks", []):
        for ln in b.get("lines", []):
            for s in ln.get("spans", []):
                if is_blue(s["color"]) and s["text"].strip():
                    out.append((tuple(s["bbox"]), float(s["size"]), s["text"]))
    return out


def open_pdf(p):
    return p if isinstance(p, pymupdf.Document) else pymupdf.open(str(p))


def page_offset(out_doc, src_doc) -> int:
    """Pages in front of the drawings (contents pages): output has more pages than the source."""
    return max(0, out_doc.page_count - src_doc.page_count)


def parse_pages(spec: str | None, n: int):
    if not spec:
        return list(range(1, n + 1))
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return [p for p in out if 1 <= p <= n]


# ---------------------------------------------------------------- masks
def runs_mask(m: np.ndarray, L: int, axis: int) -> np.ndarray:
    """True where a pixel belongs to a run of >= L consecutive True along ``axis``."""
    m = m.astype(np.int32)
    if axis == 0:
        return runs_mask(m.T, L, 1).T
    n = m.shape[1]
    if n < L:
        return np.zeros(m.shape, bool)
    cs = np.concatenate([np.zeros((m.shape[0], 1), np.int32), np.cumsum(m, axis=1)], axis=1)
    full = np.zeros(m.shape, np.int32)
    full[:, L - 1:] = ((cs[:, L:] - cs[:, :-L]) == L)          # window of L ending at j is all ink
    cf = np.concatenate([np.zeros((m.shape[0], 1), np.int32), np.cumsum(full, axis=1)], axis=1)
    idx = np.arange(n)
    hi = np.minimum(idx + L, n)                                   # covered if some full[j], j in [i, i+L-1]
    return (cf[:, hi] - cf[:, idx]) > 0


def dilate(m: np.ndarray, r: int = 1) -> np.ndarray:
    p = np.pad(m, r)
    H, W = m.shape
    out = np.zeros_like(m)
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            out |= p[dy:dy + H, dx:dx + W]
    return out


def box_overlap(a, b) -> float:
    """Intersection area / area of the smaller box."""
    ix = min(a[2], b[2]) - max(a[0], b[0]); iy = min(a[3], b[3]) - max(a[1], b[1])
    if ix <= 0 or iy <= 0:
        return 0.0
    return ix * iy / max(min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])), 1e-9)
