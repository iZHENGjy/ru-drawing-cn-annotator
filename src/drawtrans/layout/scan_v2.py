"""Scan route under output standard 3: labels, approval stamp, title-block legend and the numbered fallback,
placed by ``layout.core.Engine`` with ``layout.policy.V2``.

``scan_place.annotate`` collects the items exactly as before (map lines, extras, count labels, the stamp,
the title-block legend entries) and hands them here; drawing stays in ``scan_place`` (reportlab overlay).
Draw records produced here:

    ("lbl", ("h", fs, lines, rect), False, src_box, low)   a placed translation (no legacy leader)
    ("seg", (x0, y0), (x1, y1))                          a leader, end points on the two boxes' edges
"""
from __future__ import annotations

import re

from drawtrans.layout import core
from drawtrans.layout.policy import V2, Policy
from drawtrans.render import placelog

COLUMN_WIDTH = 0.35            # extension column of a v2 page: 0.35 x page width (max 900 pt)


def column_width(PW: float, big: bool) -> float:
    return min(PW * COLUMN_WIDTH, 900.0) if big else PW * COLUMN_WIDTH


def page_scale(PW: float, PH: float, big: bool, with_column: bool) -> float:
    return Policy.scale(PW + (column_width(PW, big) + 20 if with_column else 0), PH)


def make_engine(src_page, PW, PH, boxes, frame, fontfile, scale):
    board = core.RasterBoard.from_page(src_page, V2, boxes=boxes, frame=frame)
    return core.Engine(board, core.Metrics(fontfile), V2, scale)


def _crowd(box, boxes):
    x0, y0, x1, y1 = box
    w, h = max(x1 - x0, 1), max(y1 - y0, 1)
    X0, Y0, X1, Y1 = x0 - w, y0 - 2 * h, x1 + w, y1 + 3 * h
    return sum(1 for b in boxes if b[0] < X1 and X0 < b[2] and b[1] < Y1 and Y0 < b[3])


def place_items(items, eng: core.Engine, pl, pg, hmed, big, cov, draws, low_key="low"):
    """Place the page's items (most crowded first).  Returns (column entries [(top, text)], legend extras,
    fallback count).  Duplicate consecutive translations are drawn once (as before)."""
    boxes = eng.b.boxes
    order = sorted(items, key=lambda it: (-_crowd(it["box"], boxes), round(it["box"][1] / 4), it["box"][0]))
    done, fallback, legend_extra = [], [], []
    eng.set_budget(sum(1 for it in items if not it.get("tiny") and not it.get("stamp")))
    for it in order:
        x0, y0, x1, y1 = it["box"]
        lh = max(y1 - y0, 4.0)
        if any(z == it["zh"] and abs(ax - x0) < 25 and abs(ay - y0) < 2.0 * lh for z, ax, ay in done):
            cov["dup"].append(it["box"]); continue
        done.append((it["zh"], x0, y0))
        if it.get("stamp"):
            sb = it["sb"]
            src = core.Source("stamp", it["zh"], sb, 0.0, "stamp", own=())
            p = eng.place_one(src, extra_obstacles=(tuple(sb),))
            if p.status == "placed":
                _commit(p, it, pg, eng, cov, draws, role="stamp")
                cov["stamp"] = sb
            else:
                legend_extra.append(("В ПРОИЗВОДСТВО РАБОТ", it["zh"]))
                cov["stamp"] = sb
            continue
        bw, bh = x1 - x0, y1 - y0
        vertical = bh > 2.5 * max(bw, 1) and bh > 25
        size = bw if vertical else min(bh, 1.6 * hmed)
        title = (not vertical) and bh > hmed * 1.45 and len(it["zh"]) >= 4
        kind = "tiny" if it.get("tiny") else ("title" if title else "label")
        cx0, cy0, cx1, cy1, nwalls = pl.cell(it["box"], 120 if big else 60)
        own = tuple(b for b in boxes if b[0] >= x0 - 1.5 and b[2] <= x1 + 1.5 and b[1] >= y0 - 1.5 and b[3] <= y1 + 1.5)
        src = core.Source(str(it.get("id")), it["zh"], tuple(it["box"]), size, kind,
                          cell=(cx0, cy0, cx1, cy1) if nwalls >= 3 else None, own=own + (tuple(it["box"]),))
        p = eng.place_one(src)
        if p.status == "placed":
            _commit(p, it, pg, eng, cov, draws, role="label")
        elif kind == "tiny":
            placelog.add(route="scan", pg=pg, kind="label", role="tiny", text=it["zh"], std=3, status="legend_only",
                         src=list(it["box"]), ps=eng.s)            # the legend line explains шт./pcs.
        else:
            fallback.append((it, p.reason, src.cell))
    # numbered fallback: marker next to the source, the translation in the column at the source's height
    entries = []
    for n, (it, why, cell) in enumerate(sorted(fallback, key=lambda t: (t[0]["box"][1], t[0]["box"][0])), 1):
        band = (round(cell[1], 1), round(cell[3], 1)) if cell else None      # table row: rule above / below
        mk = core.marker(n)
        msrc = core.Source(f"m{n}", mk, tuple(it["box"]), 0.0, "tiny", own=(tuple(it["box"]),))
        mp = eng.place_one(msrc)
        ru = re.sub(r"\s+", " ", (it.get("ru") or "")).strip()
        if mp.status == "placed":
            _commit(mp, {"box": it["box"], "zh": mk}, pg, eng, cov, draws, role="marker")
            entries.append((it["box"][1], f"{mk} {it['zh']}", band))
        else:                                                       # no room even for the marker
            entries.append((it["box"][1], f"{mk} {ru[:24]}{'…' if len(ru) > 24 else ''}: {it['zh']}", band))
        placelog.add(route="scan", pg=pg, kind="label", role="fallback", text=it["zh"], std=3, status="column",
                     src=list(it["box"]), ps=eng.s, why=why)
        cov["placed"].append((it["box"], it["zh"]))
    return entries, legend_extra, len(fallback)


def layout_rows(rows, X, colw, fsP, floor_ratio, wrap, draws, busy=()):
    """Table rows continued into the column: the entries of one row band sit between that row's two rules,
    drawn as light lines across the column.  Returns (taken bands, entries that did not fit -> flowing text)."""
    by: dict = {}
    for band, text in rows:
        by.setdefault(band, []).append(text)
    taken, left = [], []
    for (y0, y1), texts in sorted(by.items(), key=lambda kv: (kv[0][1] - kv[0][0], kv[0][0])):   # narrow rows first
        text = "  ".join(texts)
        placed = False
        if any(y0 < b1 - 0.5 and b0 < y1 - 0.5 for b0, b1 in list(taken) + list(busy)):   # row used / column text there
            left.append((y0, text))
            continue
        for k in (1.0, 0.89, 0.8, floor_ratio):
            f = fsP * k
            ls = wrap(text, f, colw - 10)
            if len(ls) * f * 1.18 <= (y1 - y0) - 2:
                yy = y0 + 1.0
                for t in ls:
                    draws.append(("txt", X, yy, t, f)); yy += f * 1.18
                placed = True
                break
        if placed:
            taken.append((y0, y1))
            draws.append(("hl", X - 4, X + colw - 6, y0)); draws.append(("hl", X - 4, X + colw - 6, y1))
        else:
            left.append((y0, text))
    return taken, left


def _commit(p: core.Placement, it, pg, eng: core.Engine, cov, draws, role):
    c = p.cand
    draws.append(("lbl", ("h", c.fs, c.lines, c.rect), False, tuple(it["box"]), bool(it.get("low"))))
    if c.leader:
        draws.append(("seg", c.leader[0], c.leader[1]))
    cov["placed"].append((it["box"], it["zh"]))
    r = c.rect
    clr = V2.rule_clear(c.fs)
    placelog.add(route="scan", pg=pg, kind="label", role=role, text=" ".join(c.lines), fs=c.fs, bb=list(r),
                 src=list(it["box"]), std=3, ps=eng.s, tier=c.tier, mode=c.mode,
                 rule_px=eng.b.rule_px((r[0] - clr, r[1] - clr, r[2] + clr, r[3] + clr)),
                 ink=round(eng.b.ink_frac(r), 4),
                 leader_len=(round(c.why.get("leader", 0.0), 1) if c.leader else None),
                 leader_max=eng.lmax if c.leader else None)


def place_legend(txt, eng: core.Engine, tbbox, frame, PW, PH, pg, draws):
    """The title-block legend line: in the free band right above the title block, else outside the frame along
    its lower edge; printed >= legend floor.  Returns True when placed (else the caller puts it in the column)."""
    P, b, m = V2, eng.b, eng.m
    width0 = (tbbox[2] - tbbox[0]) if tbbox else PW * 0.38
    for f in (P.pt(P.legend_pref, eng.s) * P.print_margin, P.pt(P.legend_floor, eng.s) * P.print_margin):
        for wf in (1.0, 1.3):
            ls = m.wrap(txt, width0 * wf, f, 6)
            if not ls:
                continue
            w = max(m.width(t, f) for t in ls)
            h = len(ls) * f * P.legend_pitch
            spots = []
            if tbbox:
                for k in range(0, 8):
                    y1 = tbbox[1] - 0.3 * f - k * f
                    spots += [(tbbox[2] - w, y1 - h), (tbbox[0], y1 - h)]
            fy1 = frame[3]
            spots += [(frame[2] - w, fy1 + 1.0), (frame[0], fy1 + 1.0)]     # outside the frame, lower edge
            for x, y in spots:
                r = (x, y, x + w, y + h)
                if r[0] < 2 or r[2] > PW - 2 or r[1] < 2 or r[3] > PH - 1:
                    continue
                inside = r[3] <= frame[3]
                ok = b.free(r, f)[0] if inside else (b.rule_px(r) == 0 and b.ink_frac(r) <= P.ink_box)
                if ok:
                    b.commit(r)
                    draws.append(("lbl", ("h", f, ls, r), False, r, False))
                    placelog.add(route="scan", pg=pg, kind="label", role="legend", text=" ".join(ls), fs=f,
                                 bb=list(r), std=3, ps=eng.s, mode="above_tb" if inside else "below_frame")
                    return True
    return False
