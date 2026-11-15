"""Text-layer route under output standard 3: the same engine (``layout.core``), policy and fallback as the
scan route; drawing stays with PyMuPDF (``layout.place.Painter``).

* English slots (bilingual sheets, English replaced) are still filled where the English was, but only at
  sizes the policy allows (>= the printed floor, never below 0.8 x preferred); otherwise the label goes
  through the engine like a Russian-only label.
* Russian-only labels: ``Engine.place_one`` (below / shifted / right / left, never above, rule clearance,
  ink limit, leader rules), then a numbered marker + the translation in an extension column.
Only horizontal text takes this path; vertical labels keep the legacy placer.
"""
from __future__ import annotations

import pymupdf

from drawtrans.layout import core
from drawtrans.layout.policy import V2, Policy
from drawtrans.render import placelog

COLUMN_WIDTH = 0.35


def engine_for(page, words, extra_w: float = 0.0) -> core.Engine:
    W, H = page.rect.width, page.rect.height
    boxes = [tuple(w.rect) for w in words]
    m = V2.page_margin
    board = core.RasterBoard.from_page(page, V2, boxes=boxes, frame=(m, m, W - m, H - m))
    return core.Engine(board, core.Metrics(_fontfile()), V2, Policy.scale(W + extra_w, H))


def _fontfile():
    from drawtrans.layout import place
    return place.FONT_FILE


def slot_steps(base: float, eng: core.Engine) -> tuple:
    """fit() steps for an English slot: only sizes >= the printed preferred minimum (empty: use the engine)."""
    pref_min = V2.pt(V2.label_pref_min, eng.s) * V2.print_margin
    return tuple(k for k in V2.shrink_steps if base * k >= pref_min)


def draw(painter, eng: core.Engine, p: core.Placement, pn: int, role: str = "label"):
    """draw a placed candidate with the page's Painter and record it (standard 3)."""
    c = p.cand
    pitch = c.fs * V2.line_pitch
    painter.log = False
    for i, t in enumerate(c.lines):
        y = c.rect[1] + i * pitch
        painter.line(t, pymupdf.Rect(c.rect[0], y, c.rect[2], y + pitch), c.fs, "h")
    if c.leader:
        (x0, y0), (x1, y1) = c.leader
        painter.page.draw_line((x0, y0), (x1, y1), color=_blue(), width=0.3)
    painter.log = True
    r = c.rect
    clr = V2.rule_clear(c.fs)
    placelog.add(route="text", pg=pn, kind="label", role=role, text=" ".join(c.lines), fs=c.fs, bb=list(r),
                 src=list(p.source.rect), std=3, ps=eng.s, tier=c.tier, mode=c.mode,
                 rule_px=eng.b.rule_px((r[0] - clr, r[1] - clr, r[2] + clr, r[3] + clr)), ink=round(eng.b.ink_frac(r), 4),
                 leader_len=(round(c.why.get("leader", 0.0), 1) if c.leader else None),
                 leader_max=eng.lmax if c.leader else None)
    return [pymupdf.Rect(r)]


def _blue():
    from drawtrans.layout import place
    return place.COLOR


def place_label(eng: core.Engine, painter, zh: str, rect, size: float, own_rects, pn: int):
    """-> placed rects, or None (the label goes to the numbered column)."""
    src = core.Source("t", zh, tuple(rect), size, "label", own=tuple(tuple(r) for r in own_rects) + (tuple(rect),))
    p = eng.place_one(src)
    if p.status != "placed":
        return None
    return draw(painter, eng, p, pn)


def column(page, painter, eng: core.Engine, fallback, pn: int):
    """numbered markers next to the sources and the translations in an extension column at their heights."""
    if not fallback:
        return 0
    W, H = page.rect.width, page.rect.height
    colw = W * COLUMN_WIDTH
    entries = []
    from drawtrans.layout.place import Painter
    painter = Painter(page)                                      # markers on the original page area
    for n, (zh, rect, ru) in enumerate(sorted(fallback, key=lambda t: (t[1][1], t[1][0])), 1):
        mk = core.marker(n)
        mp = eng.place_one(core.Source(f"m{n}", mk, tuple(rect), 0.0, "tiny", own=(tuple(rect),)))
        if mp.status == "placed":
            draw(painter, eng, mp, pn, role="marker")
            entries.append((rect[1], f"{mk} {zh}"))
        else:
            entries.append((rect[1], f"{mk} {ru[:24]}: {zh}"))
        placelog.add(route="text", pg=pn, kind="label", role="fallback", text=zh, std=3, status="column",
                     src=list(rect), ps=eng.s)
    painter.flush()
    mb = page.mediabox
    page.set_mediabox(pymupdf.Rect(mb.x0, mb.y0, mb.x1 + colw + 28, mb.y1))
    scale = Policy.scale(W + colw + 28, H)
    from drawtrans.layout.place import Painter, wrap
    p2 = Painter(page)
    p2.log = False
    X = W + 16
    for k in (1.0, 0.89, 0.8, V2.column_floor * V2.print_margin / V2.column_pref):
        f = V2.pt(V2.column_pref, scale) * k
        lines, y = [], 18.0
        for top, text in sorted(entries):
            ls = wrap(text, [colw - 8] * 50, f) or wrap(text, [colw - 8] * 50, f, hard=True)
            y = max(y, top)
            for t in ls:
                lines.append((y, t)); y += f * 1.3
            y += f * 0.3
        if y <= H - 12:
            break
    for y, t in lines:
        p2.line(t, pymupdf.Rect(X, y, X + colw, y + f * 1.25), f, "h")
        placelog.add(route="text", pg=pn, kind="column", role="column", text=t, fs=f, y=y, std=3, ps=scale)
    p2.flush()
    page.draw_line((W + 6, 10), (W + 6, H - 10), color=_blue(), width=0.5)
    return len(entries)
