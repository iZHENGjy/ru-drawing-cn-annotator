"""Optional reference appendix (仅供参考): RU -> CN approximate correspondence of the materials and
standards that actually occur in a set.

    texts = collect_texts(ctx, pages)            # notes JSON + map values (+ fix.json), per page
    hits  = detect(texts)                        # only rows whose grade / standard was found
    pdf, n = render(hits, code)                  # A4 pages, reportlab, same Chinese font as the TOC
    append_to_pdf(final_pdf, out_pdf, hits, code)  # appended after the last drawing page + bookmark

Data: glossary/ref_materials.json and glossary/ref_standards.json (curated, public-knowledge
approximations; every row has a level 近似 | 无直接对应 and a note on the key difference).
Every appendix page carries the disclaimer DISCLAIMER.  Nothing here is ever an equivalence claim.
"""
import io
import json
import re
from collections import Counter
from pathlib import Path

import pymupdf
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from . import ctx as C, engine

GLOSSARY = Path(__file__).resolve().parents[2] / "glossary"
DISCLAIMER = "本对照仅供参考,不构成等同替换依据,以设计文件及适用规范为准"
SECTIONS = [("rebar", "一、钢筋 Арматура"), ("concrete", "二、混凝土强度等级 Классы бетона"),
            ("wf", "三、混凝土抗渗/抗冻 Марки W / F"), ("steel", "四、结构钢材 Конструкционная сталь"),
            ("std", "五、规范与标准 Нормы и стандарты")]


def load_refs(glossary_dir=None):
    """-> list of rows (dict: id, cat, ru, pat, cn, level, note); standards get cat 'std'."""
    g = Path(glossary_dir) if glossary_dir else GLOSSARY
    rows = []
    for fn, cat in (("ref_materials.json", None), ("ref_standards.json", "std")):
        j = json.loads((g / fn).read_text(encoding="utf-8"))
        for r in j["rows"]:
            r = dict(r)
            if cat:
                r["cat"] = cat
            rows.append(r)
    return rows


def _walk(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            if k in ("box", "src", "anchor", "conf"):
                continue
            yield from _walk(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _walk(v)


def collect_texts(ctx, pages):
    """{page: [str]} from notes/pNN.json and map/pNN.json (+ .fix.json): Chinese and Russian text of the set."""
    out = {}
    for n in pages:
        t = list(_walk(C.load_notes(ctx, n) or {}))
        mp = C.load_map(ctx, n)
        t += list(_walk(mp.get("lines") or {})) + list(_walk(mp.get("extra") or []))
        out[n] = [s.replace(" ", " ") for s in t if s and s.strip()]
    return out


def detect(texts, rows=None):
    """-> [{row, tokens: Counter, pages: sorted list}] for rows whose pattern occurs, in data order."""
    rows = rows if rows is not None else load_refs()
    hits = []
    for r in rows:
        rx = re.compile(r["pat"], re.I)
        tok, pg = Counter(), set()
        for n, strs in texts.items():
            for s in strs:
                for m in rx.finditer(s):
                    tok[re.sub(r"\s+", "", m.group(0)).replace("Ё", "Е")] += 1
                    pg.add(n)
        if tok:
            hits.append(dict(row=r, tokens=tok, pages=sorted(pg)))
    return hits


def _pages_str(pg, limit=7):
    s = ",".join(str(p) for p in pg[:limit])
    return f"原图页 {s}" + (f" 等{len(pg)}页" if len(pg) > limit else "")


def render(hits, code=""):
    """Appendix as PDF bytes + page count (0 pages / b'' when nothing was detected)."""
    if not hits:
        return b"", 0
    F = engine.FONT
    PW, PH = A4
    L, R, TOP, BOT = 36, 36, 36, 44
    cols = [("俄文 Русский", 112), ("本册出现", 92), ("国内近似(仅参考)", 112), ("主要差异与备注", PW - L - R - 316)]
    xs = [L]
    for _, w in cols:
        xs.append(xs[-1] + w)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    st = {"y": 0, "page": 0}

    def banner():
        y = PH - TOP
        c.setFillColorRGB(0.95, 0.95, 0.95); c.setStrokeColorRGB(0, 0, 0); c.setLineWidth(1.1)
        c.rect(L, y - 24, PW - L - R, 24, stroke=1, fill=1)
        c.setFillColorRGB(0, 0, 0); c.setFont(F, 10.5)
        c.drawCentredString(PW / 2, y - 16, f"【{DISCLAIMER}】")
        c.setFont(F, 7.5); c.setFillColorRGB(.3, .3, .3)
        c.drawString(L, y - 36, f"附录  俄—中材料及规范对照(仅供参考)  Справочное приложение (ориентировочно)" + (f"   {code}" if code else ""))
        return y - 44

    def footer():
        c.setFont(F, 7); c.setFillColorRGB(.25, .25, .25)
        c.drawString(L, 24, DISCLAIMER)
        c.drawRightString(PW - R, 24, f"附录 第 {st['page'] + 1} 页")

    def newpage():
        footer(); c.showPage(); st["page"] += 1
        st["y"] = banner()

    def colhead():
        y = st["y"]; hh = 15
        c.setFillColorRGB(.92, .94, .98); c.rect(L, y - hh, xs[-1] - L, hh, stroke=0, fill=1)
        c.setStrokeColorRGB(0, 0, 0); c.setLineWidth(.5); c.rect(L, y - hh, xs[-1] - L, hh, stroke=1, fill=0)
        c.setFillColorRGB(0, 0, 0); c.setFont(F, 7.8)
        for k, (t, _) in enumerate(cols):
            if k:
                c.line(xs[k], y - hh, xs[k], y)
            c.drawString(xs[k] + 3, y - 10.5, t)
        st["y"] = y - hh

    st["y"] = banner()
    c.setFont(F, 7.5); c.setFillColorRGB(0, 0, 0)
    for t in engine.wrap("本附录仅列出本册图纸文字中实际出现的钢筋/混凝土/钢材牌号与规范;未列出的项目表示本册未检出,"
                         "并非对应关系不存在。“无直接对应”表示没有可靠的一一对应关系。强度等数值仅用于说明差异。", 7.5, PW - L - R):
        c.drawString(L, st["y"] - 8, t); st["y"] -= 10
    st["y"] -= 6
    fsb = 7.4
    for cat, title in SECTIONS:
        hs = [h for h in hits if h["row"]["cat"] == cat]
        if not hs:
            continue
        if st["y"] - 60 < BOT + 14:
            newpage()
        c.setFont(F, 9.5); c.setFillColorRGB(0, 0, 0)
        c.drawString(L, st["y"] - 11, title); st["y"] -= 17
        colhead()
        for h in hs:
            r = h["row"]
            tk = [f"{t}×{n}" if n > 1 else t for t, n in sorted(h["tokens"].items(), key=lambda kv: (-kv[1], kv[0]))[:5]]
            if len(h["tokens"]) > 5:
                tk.append("…")
            cn = ("【无直接对应】" if r["level"] == "无直接对应" else "") + ("" if r["cn"] in ("—", "") else r["cn"])
            cells = [engine.wrap(r["ru"], fsb, cols[0][1] - 6),
                     engine.wrap(" ".join(tk), fsb, cols[1][1] - 6) + engine.wrap(_pages_str(h["pages"]), 6.4, cols[1][1] - 6),
                     engine.wrap(cn, fsb, cols[2][1] - 6),
                     engine.wrap(f"[{r['level']}] " + r["note"], fsb, cols[3][1] - 6)]
            hrow = max(len(x) for x in cells) * 9.2 + 5
            if st["y"] - hrow < BOT + 14:
                newpage(); colhead()
            y = st["y"]
            c.setStrokeColorRGB(0, 0, 0); c.setLineWidth(.4); c.rect(L, y - hrow, xs[-1] - L, hrow, stroke=1, fill=0)
            for k in range(1, 4):
                c.line(xs[k], y - hrow, xs[k], y)
            for k, lines in enumerate(cells):
                if k == 2:
                    c.setFillColorRGB(*engine.BLUE)
                else:
                    c.setFillColorRGB(0, 0, 0)
                yy = y - 9
                for ln in lines:
                    c.setFont(F, 6.4 if (k == 1 and ln.startswith("原图页")) else fsb)
                    c.drawString(xs[k] + 3, yy, ln); yy -= 9.2
            st["y"] = y - hrow
        st["y"] -= 10
    footer(); c.save()
    return buf.getvalue(), st["page"] + 1


def append_to_pdf(src_pdf, out_pdf, hits, code=""):
    """Append the appendix pages after the last page of ``src_pdf`` (+ bookmark); returns page count (0 = nothing added)."""
    data, n = render(hits, code)
    d = pymupdf.open(src_pdf)
    if n:
        toc = d.get_toc()
        first = d.page_count + 1
        d.insert_pdf(pymupdf.open("pdf", data))
        d.set_toc(toc + [[1, "附录 俄中对照(仅供参考)", first]])
    d.save(out_pdf, garbage=3, deflate=True)
    d.close()
    return n
