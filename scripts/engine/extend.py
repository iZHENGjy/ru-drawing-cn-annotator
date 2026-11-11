"""Extension page for pages of running text (docs/output_standard.md, section 2).

The original page keeps its content and size; the page is widened to the right and the Chinese
is set in that column:
- running text is rebuilt into paragraphs from the text layer (never per PDF line): lines that
  continue a sentence, hyphenated words and the lines of one table cell are joined;
- each paragraph is translated whole and starts at the height of its Russian paragraph;
- a table row is one entry, its cells joined by ``｜``;
- title-block / margin-stamp fields go into one ``图签译文：`` line (only if the page has them).
"""
from __future__ import annotations
import re

import pymupdf

from . import place
from .place import Painter, wrap, text_len

CYR = re.compile(r"[А-Яа-яЁё]")
BREAK_START = re.compile(r"^(?:[-–—•]\s|\d{1,2}(?:\.\d{1,2})*[.)]?\s+[А-ЯЁA-Z«\"]|[а-яё]\)\s)")
SENT_END = re.compile(r"[.:;!?»)]\s*$")
FS, FS_MIN = 9.0, 8.0
LEGEND_HEAD = "图签译文："


def _rules(page):
    """Table rules: stroked lines / rectangle edges and thin filled bars.  Filled background boxes
    (e.g. a white box behind every text line) are not rules."""
    hs, vs = [], []
    def seg(a, b):
        if abs(a.y - b.y) < 1.0 and abs(a.x - b.x) > 4:
            hs.append((min(a.x, b.x), max(a.x, b.x), (a.y + b.y) / 2))
        elif abs(a.x - b.x) < 1.0 and abs(a.y - b.y) > 4:
            vs.append(((a.x + b.x) / 2, min(a.y, b.y), max(a.y, b.y)))
    for d in page.get_drawings():
        stroked = "s" in (d.get("type") or "")
        for it in d["items"]:
            if it[0] == "l" and stroked:
                seg(it[1], it[2])
            elif it[0] == "re":
                r = it[1]
                if r.height < 1.5 or r.width < 1.5:          # a bar drawn as a thin rectangle
                    seg(pymupdf.Point(r.x0, (r.y0 + r.y1) / 2), pymupdf.Point(r.x1, (r.y0 + r.y1) / 2)) if r.height < 1.5                         else seg(pymupdf.Point((r.x0 + r.x1) / 2, r.y0), pymupdf.Point((r.x0 + r.x1) / 2, r.y1))
                elif stroked:
                    for a, b in ((r.tl, r.tr), (r.bl, r.br), (r.tl, r.bl), (r.tr, r.br)):
                        seg(a, b)
    return hs, vs


def _v_between(vs, x0, x1, y0, y1):
    return any(x0 < x < x1 and ya < y1 - 1 and yb > y0 + 1 for x, ya, yb in vs)


def _h_between(hs, x0, x1, y0, y1):
    """a horizontal rule between y0 and y1 that spans part of [x0, x1]"""
    return any(y0 - 0.5 < y < y1 + 0.5 and min(xb, x1) - max(xa, x0) > 2 for xa, xb, y in hs)


def fragments(page, vs):
    """Horizontal text lines of the text layer, split where a table rule crosses them."""
    words = page.get_text("words")
    lines = {}
    for w in words:
        lines.setdefault((w[5], w[6]), []).append(w)
    out, vert = [], []
    for ws in lines.values():
        if sum((w[3] - w[1]) > 1.2 * (w[2] - w[0]) for w in ws if len(w[4]) > 2) > len(ws) / 2:
            vert.append(sorted(ws, key=lambda w: -w[1]))   # vertical text (margin stamp): one fragment
            continue
        ws.sort(key=lambda w: w[0])
        cur = [ws[0]]
        for w in ws[1:]:
            if _v_between(vs, cur[-1][2], w[0], min(cur[-1][1], w[1]), max(cur[-1][3], w[3])):
                out.append(cur); cur = [w]
            else:
                cur.append(w)
        out.append(cur)
    frs = []
    for ws, v in [(ws, False) for ws in out] + [(ws, True) for ws in vert]:
        r = pymupdf.Rect(ws[0][:4])
        for w in ws[1:]:
            r |= pymupdf.Rect(w[:4])
        frs.append(dict(rect=r, text=" ".join(w[4] for w in ws), h=r.width if v else r.height, vert=v))
    # justified text is often written word by word: join pieces of one baseline unless a rule separates them
    frs.sort(key=lambda f: (f["vert"], round((f["rect"].y0 + f["rect"].y1) / 2), f["rect"].x0))
    merged = []
    for f in frs:
        m = merged[-1] if merged else None
        if m and not f["vert"] and not m["vert"]:
            a, b = m["rect"], f["rect"]
            ov = min(a.y1, b.y1) - max(a.y0, b.y0)
            if ov > 0.6 * min(a.height, b.height) and b.x0 >= a.x1 - 1 and not _v_between(vs, a.x1, b.x0, max(a.y0, b.y0), min(a.y1, b.y1)):
                m["rect"] = a | b; m["text"] += " " + f["text"]; m["h"] = max(m["h"], f["h"])
                continue
        merged.append(dict(f))
    return merged


def paragraphs(frs, hs, vs):
    """Join fragments into paragraphs: same cell / column, consecutive lines, no rule between,
    no paragraph break (sentence end + new start, bullet, clause number, first-line indent)."""
    frs = sorted(frs, key=lambda f: (round(f["rect"].y0, 1), f["rect"].x0))
    paras = []
    for f in frs:
        r = f["rect"]
        best = None
        for p in ([] if f["vert"] else paras):
            if p["lines"][-1]["vert"]:
                continue
            last = p["lines"][-1]["rect"]
            gap = r.y0 - last.y1
            h = max(last.height, r.height)
            if not (-0.35 * h <= gap < 0.75 * h):
                continue
            ov = min(r.x1, p["rect"].x1) - max(r.x0, p["rect"].x0)
            if ov < 0.3 * min(r.width, p["rect"].width):
                continue
            if _h_between(hs, max(r.x0, p["rect"].x0), min(r.x1, p["rect"].x1), last.y1 - 1, r.y0 + 1):
                continue
            if _v_between(vs, min(r.x0, p["rect"].x0) - 1, max(r.x0, p["rect"].x0) + 1, r.y0, r.y1) and \
                    abs(r.x0 - p["rect"].x0) > 3:
                continue
            if best is None or last.y1 > best["lines"][-1]["rect"].y1:
                best = p
        if best is not None and not _breaks(best, f):
            best["lines"].append(f); best["rect"] |= r
        else:
            paras.append(dict(lines=[f], rect=pymupdf.Rect(r)))
    for p in paras:
        p["text"] = _join([l["text"] for l in p["lines"]])
        p["size"] = sorted(l["h"] for l in p["lines"])[len(p["lines"]) // 2]
    return paras


def _breaks(p, f):
    prev = p["lines"][-1]
    t, pt = f["text"].strip(), prev["text"].strip()
    if BREAK_START.match(t):
        return True
    if re.search(r"[-,]$", pt) or re.match(r"^[а-яё(«\"]", t):
        return False                                       # the sentence continues
    right = max(p["rect"].x1, f["rect"].x1)
    width = right - min(p["rect"].x0, f["rect"].x0)
    short = prev["rect"].x1 < right - 0.15 * width          # the previous line ends early
    indent = f["rect"].x0 - p["lines"][0]["rect"].x0 > 8 if len(p["lines"]) > 1 else f["rect"].x0 - p["rect"].x0 > 8
    if SENT_END.search(pt):
        return short or indent                             # a sentence ending inside a justified line continues
    # an upper-case start after a short line without punctuation: heading, then new paragraph
    return prev["rect"].x1 < right - 0.25 * width and t[:1].isupper()


def _join(lines):
    out = ""
    for l in lines:
        l = l.strip()
        if not out:
            out = l
        elif re.search(r"[а-яёa-z]-$", out) and re.match(r"^[а-яёa-z]", l):
            out = out[:-1] + l
        else:
            out += " " + l
    return re.sub(r"\s+", " ", out).strip()


def collect(page, tm, tb):
    """-> (entries [(y, zh)], legend [(ru, zh)], stats).  Records missing keys in ``tm``."""
    hs, vs = _rules(page)
    paras = paragraphs(fragments(page, vs), hs, vs)
    body, legend, missing = [], [], 0
    for p in paras:
        if len(CYR.findall(p["text"])) < 2:
            continue
        c = (p["rect"].tl + p["rect"].br) / 2
        stamp = tb[0] if tb else pymupdf.Rect()             # left margin strip: only text set inside it
        in_tb = any(c in t for t in tb[1:]) or (p["rect"].x1 <= stamp.x1 and p["lines"][0]["vert"])
        text = p["text"]
        if in_tb:                                          # sheet code / page number are not field names
            text = " ".join(t for t in text.split() if not re.search(r"\d", t)).strip()
            if len(CYR.findall(text)) < 2:
                continue
        zh, _ = tm.translate(text, "tb" if in_tb else "body")
        if zh is None:
            missing += 1; continue
        if not zh:
            continue
        if in_tb:
            if (text, zh) not in legend:
                legend.append((text, zh))
        else:
            body.append((p, zh))
    # one entry per table row: paragraphs that start on the same line in different cells
    body.sort(key=lambda t: (t[0]["rect"].y0, t[0]["rect"].x0))
    entries = []
    for p, zh in body:
        if entries and abs(p["rect"].y0 - entries[-1][0]) < 0.6 * p["size"] and p["rect"].x0 > entries[-1][2]:
            y, z, _ = entries[-1]
            entries[-1] = (y, z + " ｜ " + zh, p["rect"].x1)
        else:
            entries.append((p["rect"].y0, zh, p["rect"].x1))
    return [(y, z) for y, z, _ in entries], legend, dict(paras=len(body) + len(legend), missing=missing)


def layout(entries, legend, PW, PH, fs, colw, align=True):
    """-> (lines [(y, text)], bottom).  Paragraphs start at their Russian line (align) or follow
    each other; the legend goes after the last paragraph, at least at the title-block height."""
    lh = fs * 1.32
    out, y = [], 18.0
    for top, zh in entries:
        ls = wrap(zh, [colw] * 400, fs) or wrap(zh, [colw] * 400, fs, hard=True)
        y = max(y, top) if align else y
        for t in ls:
            out.append((y, t)); y += lh
        y += fs * 0.35
    if legend:
        txt = LEGEND_HEAD + "；".join(f"{ru} = {zh}" for ru, zh in legend)
        ls = wrap(txt, [colw] * 100, fs * 0.9, hard=True)
        y = max(y + fs * 0.5, PH - 30 - len(ls) * lh)
        for t in ls:
            out.append((y, t, fs * 0.9)); y += lh
    return out, y


def render(page, tm, tb, dry=False):
    """Widen ``page`` and set the Chinese column.  Returns stats (paras, missing, fs, width, grown)."""
    entries, legend, st = collect(page, tm, tb)
    if dry or not (entries or legend):
        return st
    PW, PH = page.rect.width, page.rect.height
    plan = None
    for colw in (PW * 0.62, PW * 0.8, PW * 1.0):
        for fs in (FS, 8.5, FS_MIN):
            lines, bottom = layout(entries, legend, PW, PH, fs, colw)
            if bottom <= PH - 12:
                plan = (lines, bottom, fs, colw); break
        if plan:
            break
    if plan is None:                                       # relax the alignment, then grow downward
        colw, fs = PW * 1.0, FS_MIN
        lines, bottom = layout(entries, legend, PW, PH, fs, colw, align=False)
        plan = (lines, bottom, fs, colw)
    lines, bottom, fs, colw = plan
    grow = max(0.0, bottom + 12 - PH)
    mb = page.mediabox
    page.set_mediabox(pymupdf.Rect(mb.x0, mb.y0 - grow, mb.x1 + colw + 28, mb.y1))
    painter = Painter(page)
    X = PW + 16
    for item in lines:
        y, t = item[0], item[1]
        f = item[2] if len(item) > 2 else fs
        painter.line(t, pymupdf.Rect(X, y, X + colw, y + f * 1.25), f, "h")
    painter.flush()
    page.draw_line((PW + 6, 10), (PW + 6, PH + grow - 10), color=place.COLOR, width=0.5)
    st.update(fs=fs, width=round(colw), grown=round(grow))
    return st
