"""Placement engine of output standard v2 (``[scan] output_standard = 3``).

One pipeline for every translation that goes onto a page::

    Source ──gen──► Cand (by tier) ──hard filter──► score ──► best of the lowest tier ──► Placement
                                                    (no tier left: numbered marker + column entry)

* ``Source``   what to place: the translation, its source box, source size, kind (label | title | tiny |
               stamp | legend), the table cell it sits in.
* ``Board``    what is already on the page: source text ink and rules (raster, 150 dpi, dashed rules
               closed, boxes not inset), OCR word boxes, placed translations, the sheet frame.
* ``Metrics``  text width and line breaking (jieba words, no line-start punctuation) with the font that
               draws the text, so a candidate's box is the box that gets drawn.
* ``Placement`` the decision: box, lines, size, tier, mode, leader segment, score parts.

Tiers (hard order; inside a tier the lowest score wins):
    T0 below the source (titles centred) · T1 below, shifted down over source lines · T2 right / left
    · T3 the same places, smaller (never below 0.8 x the preferred size, never below the printed floor)
    · T4 wrapped over 2-3 lines · T5 leader to a free block (length, count and crossing limited)
    · T6 numbered marker next to the source + the translation in the extension column.
Every number comes from ``layout.policy``.  Pure numpy + PyMuPDF font metrics; no PDF writing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from drawtrans.layout.policy import Policy

Rect = tuple  # (x0, y0, x1, y1), PDF pt, top-left origin


# ---------------------------------------------------------------------------- records
@dataclass(frozen=True)
class Source:
    id: str
    zh: str
    rect: Rect
    size: float                       # source text height, pt
    kind: str = "label"               # label | title | tiny | stamp | vertical
    cell: Rect | None = None          # table cell around the source (>= 3 rules), if any
    own: tuple = ()                   # obstacle boxes of the source itself (ignored by the filter)


@dataclass
class Cand:
    rect: Rect
    lines: list
    fs: float
    tier: int
    mode: str
    leader: tuple | None = None       # ((x0, y0), (x1, y1))
    score: float = 0.0
    why: dict = field(default_factory=dict)


@dataclass
class Placement:
    source: Source
    status: str                       # placed | column | unplaced
    cand: Cand | None = None
    reason: str = ""


# ---------------------------------------------------------------------------- metrics
class Metrics:
    """Widths and line breaking with the drawing font (PyMuPDF metrics of the same TTF reportlab draws)."""

    def __init__(self, fontfile: str | None):
        import pymupdf
        self.font = pymupdf.Font(fontfile=fontfile) if fontfile else pymupdf.Font("cjk")

    def width(self, text: str, fs: float) -> float:
        return self.font.text_length(text, fs)

    def wrap(self, text: str, maxw: float, fs: float, max_lines: int) -> list | None:
        """greedy word wrap (jieba words, Latin runs whole, no line-start punctuation); None if it does not fit."""
        from drawtrans.layout.place import NO_LINE_START, tokens
        lines, cur = [], ""
        for t in tokens(text):
            cand = cur + t
            if self.width(cand.rstrip(), fs) <= maxw + 0.01 or not cur.strip():
                if not cur.strip() and self.width(t, fs) > maxw + 0.01:
                    return None                       # one word wider than the line
                cur = cand
                continue
            if t[:1] in NO_LINE_START and len(t.strip()) <= 2:
                cur = cand
                continue
            lines.append(cur.rstrip()); cur = t.lstrip()
            if len(lines) >= max_lines:
                return None
        if cur.strip():
            lines.append(cur.rstrip())
        return lines if len(lines) <= max_lines else None


# ---------------------------------------------------------------------------- board
def _windows(mask: np.ndarray, axis: int, win: int, need: float) -> np.ndarray:
    """pixels covered by a window of length ``win`` along ``axis`` that is at least ``need`` dark."""
    m = mask.astype(np.int32)
    cs = np.cumsum(m, axis=axis)
    pad = np.zeros_like(cs)
    if axis == 1:
        pad[:, win:] = cs[:, :-win]
    else:
        pad[win:, :] = cs[:-win, :]
    full = (cs - pad) >= need
    out = full.copy()
    for k in range(1, win):
        if axis == 1:
            out[:, :-k] |= full[:, k:]
        else:
            out[:-k, :] |= full[k:, :]
    return out & mask


class RasterBoard:
    """Obstacles of one page: rules and source ink from a grayscale raster, word boxes, placed boxes."""

    def __init__(self, gray: np.ndarray, page_w: float, page_h: float, policy: Policy,
                 boxes=(), frame: Rect | None = None):
        self.P, self.W, self.H = policy, page_w, page_h
        self.k = gray.shape[1] / page_w                      # px per pt
        dark_line = gray < 215                               # thin rules render light grey
        win = policy.rule_run_px + 2 * policy.rule_gap_px     # dashed: >= 70 % dark over the window
        need = max(policy.rule_run_px, int(0.7 * win))
        hl = _windows(dark_line, 1, win, need)
        vl = _windows(dark_line, 0, win, need)
        rules = hl | vl
        ink = (gray < 150) & ~rules
        self.rules_hv = (hl, vl)
        self.il = np.pad(rules.astype(np.float32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
        self.it = np.pad(ink.astype(np.float32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
        self.ih, self.iw = gray.shape
        self.frame = frame or (policy.page_margin, policy.page_margin, page_w - policy.page_margin,
                               page_h - policy.page_margin)
        self.boxes = [tuple(b) for b in boxes]
        self.placed: list = []
        self._g: dict = {}                                   # 40 pt buckets: ("b"|"p", i, j) -> boxes
        for b in self.boxes:
            self._add("b", b)

    @classmethod
    def from_page(cls, page, policy: Policy, boxes=(), frame=None):
        import pymupdf
        pix = page.get_pixmap(dpi=policy.raster_dpi, colorspace=pymupdf.csGRAY)
        g = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        return cls(g, page.rect.width, page.rect.height, policy, boxes, frame)

    # -- raster sums
    def _px(self, r: Rect):
        k = self.k
        x0 = int(max(0, math.floor(r[0] * k))); x1 = int(min(self.iw, math.ceil(r[2] * k)))
        y0 = int(max(0, math.floor(r[1] * k))); y1 = int(min(self.ih, math.ceil(r[3] * k)))
        return x0, y0, x1, y1

    def _sum(self, I, r: Rect) -> float:
        x0, y0, x1, y1 = self._px(r)
        if x1 <= x0 or y1 <= y0:
            return 0.0
        return float(I[y1, x1] - I[y0, x1] - I[y1, x0] + I[y0, x0])

    def rule_px(self, r: Rect) -> float:
        return self._sum(self.il, r)

    def ink_frac(self, r: Rect) -> float:
        x0, y0, x1, y1 = self._px(r)
        area = max((x1 - x0) * (y1 - y0), 1)
        return self._sum(self.it, r) / area

    def _add(self, kind, r):
        C = 40.0
        for i in range(int(r[0] // C), int(r[2] // C) + 1):
            for j in range(int(r[1] // C), int(r[3] // C) + 1):
                self._g.setdefault((kind, i, j), []).append(r)

    def _near(self, kind, r, pad):
        C = 40.0
        seen = set()
        for i in range(int((r[0] - pad) // C), int((r[2] + pad) // C) + 1):
            for j in range(int((r[1] - pad) // C), int((r[3] + pad) // C) + 1):
                for b in self._g.get((kind, i, j), ()):
                    if id(b) not in seen:
                        seen.add(id(b))
                        yield b

    def commit(self, r: Rect):
        r = tuple(r)
        self.placed.append(r)
        self._add("p", r)

    # -- hard filter
    def free(self, r: Rect, fs: float, own=(), extra_obstacles=()) -> tuple[bool, str]:
        P = self.P
        fx0, fy0, fx1, fy1 = self.frame
        if r[0] < fx0 or r[1] < fy0 or r[2] > fx1 or r[3] > fy1:
            return False, "outside"
        for b in list(self._near("b", r, 0.3)) + list(extra_obstacles):
            if b in own:
                continue
            if r[0] < b[2] + 0.3 and b[0] < r[2] + 0.3 and r[1] < b[3] + 0.3 and b[1] < r[3] + 0.3:
                return False, "text"
        g = P.gap_placed
        for b in self._near("p", r, g):
            if r[0] < b[2] + g and b[0] < r[2] + g and r[1] < b[3] + g and b[1] < r[3] + g:
                return False, "placed"
        c = P.rule_clear(fs)
        if self.rule_px((r[0] - c, r[1] - c, r[2] + c, r[3] + c)) > 0:
            return False, "rule"
        if self.ink_frac(r) > P.ink_box:
            return False, "ink"
        return True, ""

    def segment_ok(self, p0, p1, ignore=()) -> bool:
        """a leader: no source text crossed (ink share <= policy), no word box or placed box crossed."""
        n = max(2, int(math.dist(p0, p1) * self.k))
        hits = 0
        inner = 0
        for i in range(1, n):
            t = i / n
            x, y = p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t
            if any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in ignore):
                continue
            inner += 1
            pt = (x, y, x, y)
            for kind in ("b", "p"):
                for b in self._near(kind, pt, 0):
                    if b[0] < x < b[2] and b[1] < y < b[3]:
                        return False
            px, py = int(x * self.k), int(y * self.k)
            if 0 <= px < self.iw and 0 <= py < self.ih and self._sum(self.it, (x - 0.5, y - 0.5, x + 0.5, y + 0.5)) > 0:
                hits += 1
        return inner == 0 or hits / inner <= self.P.leader_ink_max


# ---------------------------------------------------------------------------- engine
def _nearest(a: Rect, b: Rect):
    """closest points of two rectangles (leader end points)."""
    ax = min(max(b[0], a[0]), a[2]) if not (a[0] <= b[2] and b[0] <= a[2]) else None
    def clamp(v, lo, hi):
        return min(max(v, lo), hi)
    cx = (max(a[0], b[0]) + min(a[2], b[2])) / 2 if (a[0] <= b[2] and b[0] <= a[2]) else None
    cy = (max(a[1], b[1]) + min(a[3], b[3])) / 2 if (a[1] <= b[3] and b[1] <= a[3]) else None
    pa = (clamp(cx if cx is not None else (b[0] + b[2]) / 2, a[0], a[2]),
          clamp(cy if cy is not None else (b[1] + b[3]) / 2, a[1], a[3]))
    pb = (clamp(pa[0], b[0], b[2]), clamp(pa[1], b[1], b[3]))
    del ax
    return pa, pb


class Engine:
    """places Sources on one page; ``scale`` is the print factor of the page (policy.scale)."""

    def __init__(self, board: RasterBoard, metrics: Metrics, policy: Policy, scale: float):
        self.b, self.m, self.P, self.s = board, metrics, policy, scale
        self.leaders = 0
        self.lmax = policy.leader_max(board.W, board.H)

    def sizes(self, src: Source) -> list[float]:
        P = self.P
        floor = P.pt(P.stamp_floor if src.kind == "stamp" else P.label_floor, self.s)
        pref = max(P.label_ratio * src.size, P.pt(P.stamp_floor if src.kind == "stamp" else P.label_pref_min, self.s))
        out = []
        for k in P.shrink_steps:
            f = pref * k
            if f < floor - 1e-6:
                if not out or out[-1] > floor + 1e-6:
                    out.append(floor)
                break
            out.append(f)
        return out

    def _box(self, x, y, lines, fs):
        w = max(self.m.width(t, fs) for t in lines)
        return (x, y, x + w, y + len(lines) * fs * self.P.line_pitch)

    def _gen(self, src: Source):
        """candidates in tier order: (tier, mode, rect, lines, fs, leader)"""
        P = self.P
        r = src.rect
        sizes = self.sizes(src)
        lh = max(r[3] - r[1], 4.0)
        title = src.kind == "title"
        for si, fs in enumerate(sizes):
            shrink = si > 0
            for nl in range(1, P.max_label_lines + 1):
                if nl == 1:
                    layouts = [[src.zh]]
                else:
                    widths = sorted({max(r[2] - r[0], 6 * fs), (src.cell[2] - src.cell[0] - 2) if src.cell else 0} - {0})
                    layouts = [ls for w in widths if (ls := self.m.wrap(src.zh, w, fs, nl)) and len(ls) == nl]
                for ls in layouts:
                    w = max(self.m.width(t, fs) for t in ls)
                    h = len(ls) * fs * P.line_pitch
                    gap = P.gap_below * fs
                    tier_base = 4 if nl > 1 else (3 if shrink else 0)
                    x_below = (r[0] + r[2]) / 2 - w / 2 if title else r[0]
                    yield (tier_base if tier_base else 0, "below", (x_below, r[3] + gap, x_below + w, r[3] + gap + h), ls, fs)
                    for k in range(1, P.shift_lines + 1):
                        y = r[3] + gap + k * lh
                        yield (tier_base if tier_base else 1, "below_shift", (x_below, y, x_below + w, y + h), ls, fs)
                    if src.kind != "stamp":
                        gx = 0.3 * fs
                        yield (tier_base if tier_base else 2, "right", (r[2] + gx, r[1], r[2] + gx + w, r[1] + h), ls, fs)
                        yield (tier_base if tier_base else 2, "left", (r[0] - gx - w, r[1], r[0] - gx, r[1] + h), ls, fs)
                    else:                               # a stamp: outside, any side (above allowed: it is no label)
                        yield (tier_base if tier_base else 1, "above", (r[0], r[1] - gap - h, r[0] + w, r[1] - gap), ls, fs)
                        yield (tier_base if tier_base else 2, "right", (r[2] + gap, r[1], r[2] + gap + w, r[1] + h), ls, fs)
                        yield (tier_base if tier_base else 2, "left", (r[0] - gap - w, r[1], r[0] - gap, r[1] + h), ls, fs)

    def _score(self, src: Source, rect: Rect, ls, fs, sizes) -> tuple[float, dict]:
        r = src.rect
        dx = max(r[0] - rect[2], rect[0] - r[2], 0); dy = max(r[1] - rect[3], rect[1] - r[3], 0)
        dist = math.hypot(dx, dy) / max(src.size, 1.0)
        size_loss = ((sizes[0] - fs) / sizes[0]) ** 2
        c = self.P.rule_clear(fs)
        near = 0.0
        for k in (2, 3):                                     # softly prefer room beyond the hard clearance
            if self.b.rule_px((rect[0] - k * c, rect[1] - k * c, rect[2] + k * c, rect[3] + k * c)) > 0:
                near += 0.5
        align = 0.0 if abs(rect[0] - r[0]) < 1.0 else 0.3
        parts = {"dist": round(dist, 3), "size": round(size_loss * 8, 3), "wrap": len(ls) - 1, "near_rule": near,
                 "ink": round(self.b.ink_frac(rect) * 20, 3), "align": align}
        return sum(parts.values()), parts

    def place_one(self, src: Source, extra_obstacles=()) -> Placement:
        sizes = self.sizes(src)
        best: dict[int, Cand] = {}
        reasons: dict[str, int] = {}
        for tier, mode, rect, ls, fs in self._gen(src):
            if tier in best and best[tier].score <= 0:
                continue
            ok, why = self.b.free(rect, fs, src.own, extra_obstacles)
            if not ok:
                reasons[why] = reasons.get(why, 0) + 1
                continue
            sc, parts = self._score(src, rect, ls, fs, sizes)
            if tier not in best or sc < best[tier].score:
                best[tier] = Cand(rect, list(ls), fs, tier, mode, None, sc, parts)
        if best:
            c = best[min(best)]
            self.b.commit(c.rect)
            return Placement(src, "placed", c)
        c = self._leader(src, sizes, extra_obstacles)
        if c is not None:
            self.b.commit(c.rect)
            self.leaders += 1
            return Placement(src, "placed", c)
        return Placement(src, "column", None, ",".join(f"{k}:{v}" for k, v in sorted(reasons.items())))

    def _leader(self, src: Source, sizes, extra_obstacles) -> Cand | None:
        P = self.P
        if src.kind in ("stamp", "tiny") or self.leaders >= P.leaders_per_page:
            return None
        r = src.rect
        best = None
        for fs in sizes[:2]:
            for nl in (1, 2):
                ls = [src.zh] if nl == 1 else self.m.wrap(src.zh, max(r[2] - r[0], 6 * fs), fs, 2)
                if not ls:
                    continue
                w = max(self.m.width(t, fs) for t in ls); h = len(ls) * fs * P.line_pitch
                step = max(fs * 0.5, 2.0)
                n = int(self.lmax / step) + 1
                for i in range(-n, n + 1):
                    for j in range(-n, n + 1):
                        x, y = r[0] + i * step, r[3] + j * step
                        rect = (x, y, x + w, y + h)
                        if rect[3] <= r[1] + 0.5 and rect[0] < r[2] and r[0] < rect[2]:
                            continue                        # never above the source
                        pa, pb = _nearest(r, rect)
                        L = math.dist(pa, pb)
                        if L > self.lmax or L < 0.5 or (best and L >= best[0]):
                            continue
                        if not self.b.free(rect, fs, src.own, extra_obstacles)[0]:
                            continue
                        if not self.b.segment_ok(pa, pb, ignore=(r, rect)):
                            continue
                        best = (L, Cand(rect, list(ls), fs, 5, "leader", (pa, pb), L / max(src.size, 1), {"leader": round(L, 1)}))
            if best:
                break
        return best[1] if best else None


CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def marker(n: int) -> str:
    """①…⑳, then (21) (22) …"""
    return CIRCLED[n - 1] if 1 <= n <= len(CIRCLED) else f"({n})"
