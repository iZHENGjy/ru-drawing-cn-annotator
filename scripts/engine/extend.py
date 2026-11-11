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
            if _h_between(hs, max(r.x0, p["rect"].x0), min(r.x1, p["rect"].x1), min(last.y1 - 1, (last.y0 + last.y1) / 2 + 1), max(r.y0 + 1, (r.y0 + r.y1) / 2 - 1)):
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
    indent = f["rect"].x0 - min(l["rect"].x0 for l in p["lines"]) > 8
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


TB_WORDS = {"изм", "лист", "№", "документа", "док", "подпись", "подп", "дата", "кол", "уч", "колуч", "листов", "n"}
RULE_COLOR = (0.62, 0.72, 0.95)


def _tb_fields_only(text):
    """a line of sheet-frame field names only (Изм. Лист № документа Подпись Дата)"""
    ws = [w for w in re.split(r"[\s.]+", text.lower()) if w and not re.search(r"\d", w)]
    return bool(ws) and all(w in TB_WORDS for w in ws)


def collect(page, tm, tb):
    """-> (entries, legend, stats).  entries: {y, zh, bot (table row: its lower rule) or None};
    stats["rules"]: the table rules to continue into the column.  Records missing keys in ``tm``."""
    hs, vs = _rules(page)
    frs = fragments(page, vs)
    paras = paragraphs(frs, hs, vs)
    body, legend, missing = [], [], 0
    for p in paras:
        c = (p["rect"].tl + p["rect"].br) / 2
        stamp = tb[0] if tb else pymupdf.Rect()             # left margin strip: only text set inside it
        in_tb = any(c in t for t in tb[1:]) or (p["rect"].x1 <= stamp.x1 and p["lines"][0]["vert"]) \
            or (c.y > page.rect.height * 0.85 and _tb_fields_only(p["text"]))
        text = p["text"]
        if len(CYR.findall(text)) < 2:                     # codes / numbers: kept as they are (table cells)
            if not in_tb and text.strip():
                body.append((p, None))
            continue
        if in_tb:                                          # sheet code / page number are not field names
            text = " ".join(t for t in text.split() if not re.search(r"\d", t)).strip()
            if len(CYR.findall(text)) < 2:
                continue
        zh, _ = tm.translate(text, "tb" if in_tb else "body")
        if zh is None:
            missing += 1; continue
        if not zh:
            if not in_tb:
                body.append((p, None))                     # 'keep' (names, standard numbers): shown as is in a row
            continue
        if in_tb:
            if (text, zh) not in legend:
                legend.append((text, zh))
        else:
            body.append((p, zh))

    # table rows: the cells between the same two horizontal rules (rule positions clustered, so
    # that the cells of one row agree on them even if the rules are drawn piece by piece)
    def snap(vals):
        out = {}
        for v in sorted(vals):
            out[v] = out[prev] if out and v - prev < 2.0 else v
            prev = v
        return out
    hsnap = snap({y for _, _, y in hs})
    def row_of(r, size):
        up = [hsnap[y] for xa, xb, y in hs if y <= r.y0 + 2.5 and min(xb, r.x1) - max(xa, r.x0) > 2]
        dn = [hsnap[y] for xa, xb, y in hs if y >= r.y1 - 2.5 and min(xb, r.x1) - max(xa, r.x0) > 2]
        if not (up and dn) or min(dn) - max(up) > 9 * max(size, 6):
            return None                                     # not a table row (e.g. text between table and title block)
        return (round(max(up), 1), round(min(dn), 1))
    body.sort(key=lambda t: (t[0]["rect"].y0, t[0]["rect"].x0))
    rows, plain = {}, []
    for p, zh in body:
        key = row_of(p["rect"], p["size"])
        if key is None:
            if zh is None:
                continue
            plain.append(dict(y=p["rect"].y0, zh=zh, x0=p["rect"].x0, x1=p["rect"].x1, size=p["size"]))
        else:
            rows.setdefault(key, []).append((p, zh))
    # a merged cell spanning several rows joins the first row it spans
    for key in sorted(rows, key=lambda k: k[1] - k[0], reverse=True):
        inner = [k for k in rows if k != key and key[0] - 0.5 <= k[0] and k[1] <= key[1] + 0.5]
        if inner:
            rows[min(inner)].extend(rows.pop(key))
    entries, rules = [], set()
    frame = 0.6 * page.rect.height                          # the sheet frame is not a table rule
    for (top, bot), cells in list(rows.items()):
        x0 = min(p["rect"].x0 for p, _ in cells)
        vx = sorted(set(snap({x for x, ya, yb in vs if yb - ya < frame
                              and min(yb, bot) - max(ya, top) > 0.6 * (bot - top)}).values()))
        if len(vx) < 2 or all(zh is None for _, zh in cells):   # no cell walls / nothing to translate
            for p, zh in cells:
                if zh is None:
                    continue
                plain.append(dict(y=p["rect"].y0, zh=zh, x0=p["rect"].x0, x1=p["rect"].x1, size=p["size"]))
            continue
        def col(r):
            return sum(1 for x in vx if x < (r.x0 + r.x1) / 2)
        bycol = {}
        for p, zh in sorted(cells, key=lambda t: (col(t[0]["rect"]), t[0]["rect"].y0)):
            bycol.setdefault(col(p["rect"]), []).append(
                zh if zh is not None else re.sub(r"(\d)- (\d)", r"\1-\2", p["text"]))   # ГОСТ 28638- / 90
        # the row number: a number alone in the first column of this row (not repeated as a field)
        num = None
        first = min(bycol)
        if len(bycol[first]) == 1 and re.fullmatch(r"\d{1,3}\.?", bycol[first][0].strip()):
            num = bycol.pop(first)[0].strip().rstrip(".")
        # every column is shown: translated cells in Chinese, the others as in the original
        zh = " | ".join(" ".join(z) for _, z in sorted(bycol.items()))
        entries.append(dict(y=top, zh=(f"{num}. " if num else "") + zh, bot=bot))
        rules |= {top, bot}
    # paragraphs outside tables: those that start on the same line are one entry
    plain.sort(key=lambda e: (e["y"], e["x0"]))
    last = None
    for e in plain:
        if last is not None and abs(e["y"] - last["y"]) < 0.6 * e["size"] and e["x0"] > last["x1"]:
            last["zh"] += " | " + e["zh"]; last["x1"] = e["x1"]
        else:
            last = dict(y=e["y"], zh=e["zh"], bot=None, x1=e["x1"])
            entries.append(last)
    entries.sort(key=lambda e: e["y"])
    return entries, legend, dict(paras=len(body) + len(legend), missing=missing, rules=sorted(rules))


def layout(entries, legend, PW, PH, fs, colw, align=True):
    """-> (lines [(y, text, fs)], bottom, ok).  A table row sits between its two rules (wrapped,
    the size reduced to FS_MIN - 1.5 at most); other paragraphs start at their Russian line
    (align) or follow each other; the legend goes last, at least at the title-block height."""
    lh = 1.32
    out, y, ok = [], 18.0, True
    for e in entries:
        if e.get("bot") is not None and align:
            top, bot = e["y"] + 1.5, e["bot"] - 1.0
            f = fs
            while True:
                ls = wrap(e["zh"], [colw] * 60, f) or wrap(e["zh"], [colw] * 60, f, hard=True)
                if top + len(ls) * f * lh <= bot + 0.5 or f <= FS_MIN - 1.5:
                    break
                f -= 0.5
            if top + len(ls) * f * lh > bot + 0.5 or y > bot:
                ok = False
            yy = top
            for t in ls:
                out.append((yy, t, f)); yy += f * lh
            y = max(y, bot + 1.0)
            continue
        ls = wrap(e["zh"], [colw] * 400, fs) or wrap(e["zh"], [colw] * 400, fs, hard=True)
        y = max(y, e["y"]) if align else y
        for t in ls:
            out.append((y, t, fs)); y += fs * lh
        y += fs * 0.35
    if legend:
        txt = LEGEND_HEAD + "；".join(f"{ru} = {zh}" for ru, zh in legend)
        ls = wrap(txt, [colw] * 100, fs * 0.9, hard=True)
        y = max(y + fs * 0.5, PH - 30 - len(ls) * fs * lh)
        for t in ls:
            out.append((y, t, fs * 0.9)); y += fs * lh
    return out, y, ok


def render(page, tm, tb, dry=False):
    """Widen ``page`` and set the Chinese column.  Returns stats (paras, missing, fs, width, grown)."""
    entries, legend, st = collect(page, tm, tb)
    rules = st.pop("rules", [])
    if dry or not (entries or legend):
        return st
    PW, PH = page.rect.width, page.rect.height
    plan = fit = None
    for colw in (PW * 0.62, PW * 0.8, PW * 1.0):
        for fs in (FS, 8.5, FS_MIN):
            lines, bottom, ok = layout(entries, legend, PW, PH, fs, colw)
            if bottom <= PH - 12 and ok:
                plan = (lines, bottom, fs, colw); break
            if bottom <= PH - 12 and fit is None:
                fit = (lines, bottom, fs, colw)        # fits; only some tight table rows use a smaller size
        if plan:
            break
    plan = plan if plan and (fit is None or plan[3] == fit[3]) else (fit or plan)
    if plan is None:                                       # keep the table rows if the page still fits
        colw, fs = PW * 1.0, FS_MIN
        lines, bottom, ok = layout(entries, legend, PW, PH, fs, colw)
        if bottom > PH - 12:                               # relax the alignment, then grow downward
            lines, bottom, ok = layout(entries, legend, PW, PH, fs, colw, align=False)
            rules = []
        plan = (lines, bottom, fs, colw)
    lines, bottom, fs, colw = plan
    grow = max(0.0, bottom + 12 - PH)
    mb = page.mediabox
    page.set_mediabox(pymupdf.Rect(mb.x0, mb.y0 - grow, mb.x1 + colw + 28, mb.y1))
    X = PW + 16
    for y in rules:                                        # the table continues into the column (light rules)
        page.draw_line((X - 4, y), (X + colw + 4, y), color=RULE_COLOR, width=0.35)
    painter = Painter(page)
    for y, t, f in lines:
        painter.line(t, pymupdf.Rect(X, y, X + colw, y + f * 1.25), f, "h")
    painter.flush()
    page.draw_line((PW + 6, 10), (PW + 6, PH + grow - 10), color=place.COLOR, width=0.5)
    st.update(fs=fs, width=round(colw), grown=round(grow))
    return st
