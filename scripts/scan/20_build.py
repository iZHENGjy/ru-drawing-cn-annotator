"""Step 4 — build the bilingual PDF from the maps (and a clickable table of contents).

    python scripts/scan/20_build.py [--config project.toml] [--pages 3,5] [--no-toc]

Output: output/<code>_中文对照版.pdf (full set) or output/<code>_test.pdf (--pages), plus
work/review/build_report.tsv: one row per decision that is not a plain placement
(引线 = leader, 入对照 = moved to the title-block legend, 加宽 = page widened for notes,
未放置 = could not be placed — must be 0 before delivery).
"""
import argparse
import io
import logging
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.getLogger("pypdf").setLevel(logging.ERROR)
import pymupdf  # noqa: E402
from pypdf import PdfWriter  # noqa: E402
from scan import ctx as C, engine  # noqa: E402


def build(ctx, pages, out_pdf, report_tsv=None, verbose=True):
    report, stats = [], []
    w = PdfWriter()
    for n in pages:
        t0 = time.time()
        page, cnt = engine.annotate(ctx.cfg.code, n, C.load_map(ctx, n), C.load_notes(ctx, n), report)
        w.add_page(page)
        stats.append(f"{n}:{cnt}({time.time() - t0:.0f}s)")
    with open(out_pdf, "wb") as fh:
        w.write(fh)
    cnt = Counter(r[2] for r in report if not str(r[2]).isdigit())
    if verbose:
        print(out_pdf); print("  ", " ".join(stats)); print("  ", dict(cnt))
    if report_tsv:
        Path(report_tsv).parent.mkdir(parents=True, exist_ok=True)
        with open(report_tsv, "w", encoding="utf-8") as fh:
            for r in report:
                fh.write("\t".join(map(str, r)) + "\n")
    return report


def add_toc(ctx, src_pdf, out_pdf):
    """Clickable contents pages (sheet code, Russian + Chinese title, format) + bookmarks.
    Titles come from notes/pNN.json: sheet_code, title_ru, title_zh, format."""
    import json
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    F = engine.FONT
    d = pymupdf.open(src_pdf)
    rows = []
    for i in range(1, d.page_count + 1):
        j = C.load_notes(ctx, i) or {}
        rows.append((i, j.get("sheet_code") or "", j.get("title_ru") or "", j.get("title_zh") or "",
                     (j.get("format") or "").replace("Формат", "").strip()))
    PW, PH = A4
    L, R, TOP, BOT = 42, 28, 40, 40
    cols = [("页次\nЛист", 34), ("图号\nОбозначение", 150), ("图名\nНаименование", PW - L - R - 236), ("图幅\nФормат", 52)]
    xs = [L]
    for _, cw in cols:
        xs.append(xs[-1] + cw)
    buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=A4)
    links, state = [], {"y": 0, "page": 0}

    def header(first):
        y = PH - TOP
        if first:
            c.setFont(F, 15); c.setFillColorRGB(0, 0, 0); c.drawCentredString(PW / 2, y - 14, "目  录")
            c.setFont(F, 9.5); c.drawCentredString(PW / 2, y - 30, "Содержание / Contents")
            c.setFillColorRGB(*engine.BLUE); c.setFont(F, 9)
            c.drawCentredString(PW / 2, y - 46, f"{ctx.cfg.code}  {ctx.cfg.toc_title_zh}")
            c.setFillColorRGB(.35, .35, .35); c.setFont(F, 7.5); c.drawCentredString(PW / 2, y - 58, ctx.cfg.toc_title_ru)
            y -= 70
        hh = 24
        c.setFillColorRGB(.92, .94, .98); c.rect(L, y - hh, xs[-1] - L, hh, stroke=0, fill=1)
        c.setStrokeColorRGB(0, 0, 0); c.setLineWidth(.6); c.rect(L, y - hh, xs[-1] - L, hh, stroke=1, fill=0)
        for k, (t, _) in enumerate(cols):
            if k:
                c.line(xs[k], y - hh, xs[k], y)
            a, b = t.split("\n")
            c.setFillColorRGB(*engine.BLUE); c.setFont(F, 8.5); c.drawCentredString((xs[k] + xs[k + 1]) / 2, y - 10, a)
            c.setFillColorRGB(0, 0, 0); c.setFont(F, 6.8); c.drawCentredString((xs[k] + xs[k + 1]) / 2, y - 20, b)
        state["y"] = y - hh

    header(True)
    for (i, sc, ru, zh, fmt) in rows:
        wt = cols[2][1] - 8
        lr, lz, lc = engine.wrap(ru, 7.2, wt), engine.wrap(zh, 8.2, wt), engine.wrap(sc, 7.2, cols[1][1] - 6)
        h = max(len(lr) * 9 + len(lz) * 10.5, len(lc) * 9) + 7
        if state["y"] - h < BOT + 14:
            c.setFont(F, 7); c.setFillColorRGB(.4, .4, .4); c.drawRightString(PW - R, 22, f"目录 第 {state['page'] + 1} 页")
            c.showPage(); state["page"] += 1; header(False)
        y = state["y"]
        c.setStrokeColorRGB(0, 0, 0); c.setLineWidth(.4); c.rect(L, y - h, xs[-1] - L, h, stroke=1, fill=0)
        for k in range(1, 4):
            c.line(xs[k], y - h, xs[k], y)
        c.setFillColorRGB(0, 0, 0); c.setFont(F, 8.5); c.drawCentredString((xs[0] + xs[1]) / 2, y - h / 2 - 3, str(i))
        c.setFont(F, 7.2); yy = y - 10
        for t in lc:
            c.drawString(xs[1] + 3, yy, t); yy -= 9
        yy = y - 10
        for t in lr:
            c.drawString(xs[2] + 4, yy, t); yy -= 9
        c.setFont(F, 8.2); c.setFillColorRGB(*engine.BLUE)
        for t in lz:
            c.drawString(xs[2] + 4, yy - 1, t); yy -= 10.5
        c.setFillColorRGB(0, 0, 0); c.setFont(F, 7.5); c.drawCentredString((xs[3] + xs[4]) / 2, y - h / 2 - 3, fmt)
        links.append((state["page"], pymupdf.Rect(L, PH - y, xs[-1], PH - (y - h)), i))
        state["y"] = y - h
    c.setFont(F, 7); c.setFillColorRGB(.4, .4, .4)
    c.drawString(L, 22, "点击任一行可跳转到对应图纸页;左侧书签同样可导航。蓝色为中文译文。")
    c.drawRightString(PW - R, 22, f"目录 第 {state['page'] + 1} 页")
    c.save()
    toc = pymupdf.open("pdf", buf.getvalue()); k = toc.page_count
    out = pymupdf.open(); out.insert_pdf(toc); out.insert_pdf(d)
    for tp, rect, target in links:
        out[tp].insert_link({"kind": pymupdf.LINK_GOTO, "from": rect, "page": k + target - 1, "to": pymupdf.Point(0, 0)})
    bm = [[1, "目录 Содержание", 1]]
    for (i, sc, ru, zh, fmt) in rows:
        bm.append([1, f"{i}  {sc.split('-')[-1] if sc else ''}  {zh}"[:90], k + i])
    out.set_toc(bm)
    out.save(out_pdf, garbage=3, deflate=True)
    return k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--pages")
    ap.add_argument("--no-toc", action="store_true")
    a = ap.parse_args()
    cfg = C.load(a.config)
    ctx = C.Ctx(cfg); ctx.mkdirs(); engine.init(ctx)
    n = pymupdf.open(cfg.pdf).page_count
    if a.pages:
        pages = []
        for part in a.pages.split(","):
            x, _, y = part.partition("-"); pages += list(range(int(x), int(y or x) + 1))
        build(ctx, pages, Path(cfg.output) / f"{cfg.code}_test.pdf", ctx.w / "review" / "build_report_test.tsv")
        return
    body = Path(cfg.output) / f"{cfg.code}_body.pdf"
    rep = build(ctx, list(range(1, n + 1)), body, ctx.w / "review" / "build_report.tsv")
    final = Path(cfg.output) / f"{cfg.code}_中文对照版.pdf"
    if a.no_toc:
        body.replace(final)
    else:
        k = add_toc(ctx, body, final); body.unlink()
        print(f"   + {k} contents page(s) -> {final}")
    lost = [r for r in rep if r[2] in ("未放置",)]
    if lost:
        print(f"!! {len(lost)} label(s) could not be placed — see build_report.tsv")


if __name__ == "__main__":
    main()
