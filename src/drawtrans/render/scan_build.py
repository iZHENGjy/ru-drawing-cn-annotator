"""Step 4 — build the bilingual PDF from the maps (and a clickable table of contents).

    python scripts/scan/20_build.py [--config project.toml] [--pages 3,5] [--no-toc] [--appendix] [--compact]

Opt-in (also project.toml ``[output] appendix = true`` / ``compact = true``; default off = unchanged output):
  --appendix  reference pages (仅供参考) at the end: RU->CN approximate grades / standards found in the set
  --compact   精简版: no per-word 根/间距/件 count labels (and no legend line for them)
Low-confidence Chinese is marked with a dotted underline whenever work/map/pNN.meta.json exists.

Output: output/<code>_中文对照版.pdf (full set) or output/<code>_test.pdf (--pages), plus
work/review/build_report.tsv: one row per decision that is not a plain placement
(引线 = leader, 入对照 = moved to the title-block legend, 加宽 = page widened for notes,
未放置 = could not be placed — must be 0 before delivery).
"""
import argparse
import io
import logging
import time
from collections import Counter
from pathlib import Path

from drawtrans import pagesel

logging.getLogger("pypdf").setLevel(logging.ERROR)
import pymupdf  # noqa: E402
from pypdf import PdfWriter  # noqa: E402

from drawtrans.config import scanroute as C  # noqa: E402
from drawtrans.layout import scan_place as engine  # noqa: E402

CODE_FILES = ["layout/scan_place.py", "config/scanroute.py", "render/scan_build.py", "languages/__init__.py", "render/placelog.py"]


def _code_hash() -> str:
    import hashlib
    h = hashlib.sha256()
    root = Path(__file__).resolve().parents[1]
    for f in CODE_FILES:
        h.update((root / f).read_bytes())
    return h.hexdigest()[:16]


def page_key(ctx, n, code_hash, cfg_text) -> str:
    """hash of everything annotate() reads for page n: the page is rebuilt only when one of them changes."""
    import hashlib
    h = hashlib.sha256(f"{code_hash}|{n}|{engine.OPTS}|{cfg_text}".encode("utf-8"))
    for f in (ctx.map(n), ctx.fix(n), ctx.f("map", n, ".meta.json"), ctx.notes(n), ctx.lines(n), ctx.ocr(n), ctx.rapid(n)):
        h.update(f.read_bytes() if f.exists() else b"-")
        h.update(b"|")
    return h.hexdigest()[:24]


_W = {}


def _annotate_one(config, compact, n, cache_file):
    """worker: annotate one page -> one-page PDF (cached), report rows, placement records, count."""
    import json

    from drawtrans.render import placelog
    if "ctx" not in _W:
        cfg = C.load(config)
        _W["ctx"] = C.Ctx(cfg); engine.init(_W["ctx"])
    engine.OPTS["compact"] = compact
    ctx = _W["ctx"]
    report = []
    placelog.reset()
    page, cnt = engine.annotate(ctx.cfg.code, n, C.load_map(ctx, n), C.load_notes(ctx, n), report)
    w = PdfWriter(); w.add_page(page)
    buf = io.BytesIO(); w.write(buf)
    side = {"report": [list(map(str, r)) for r in report], "records": list(placelog.RECORDS), "cnt": cnt}
    Path(cache_file).parent.mkdir(parents=True, exist_ok=True)
    Path(cache_file).write_bytes(buf.getvalue())
    Path(cache_file).with_suffix(".json").write_text(json.dumps(side, ensure_ascii=False), encoding="utf-8")
    placelog.reset()
    return n


def build(ctx, pages, out_pdf, report_tsv=None, verbose=True, config=None):
    """Annotate the pages.  With ``config`` the pages run in a process pool and every page is cached in
    work/review/page_cache by a hash of its inputs, so a rebuild only redoes the pages that changed."""
    import json
    import os
    from concurrent.futures import ProcessPoolExecutor

    from pypdf import PdfReader

    from drawtrans.render import placelog
    report, stats = [], []
    w = PdfWriter()
    if config is None:
        for n in pages:
            t0 = time.time()
            page, cnt = engine.annotate(ctx.cfg.code, n, C.load_map(ctx, n), C.load_notes(ctx, n), report)
            w.add_page(page)
            stats.append(f"{n}:{cnt}({time.time() - t0:.0f}s)")
    else:
        code, cfg_text = _code_hash(), Path(config).read_text(encoding="utf-8")
        cdir = ctx.w / "review" / "page_cache"
        files = {n: cdir / f"p{n:02d}_{page_key(ctx, n, code, cfg_text)}.pdf" for n in pages}
        todo = [n for n in pages if not (files[n].exists() and files[n].with_suffix(".json").exists())]
        nw = int(os.environ.get("RUDRAW_BUILD_WORKERS") or max(1, min(8, (os.cpu_count() or 2) // 2, len(todo))))
        t0 = time.time()
        if nw > 1 and len(todo) > 1:
            with ProcessPoolExecutor(nw) as ex:
                list(ex.map(_annotate_one, [str(config)] * len(todo), [engine.OPTS["compact"]] * len(todo), todo,
                            [str(files[n]) for n in todo]))
        else:
            for n in todo:
                _annotate_one(str(config), engine.OPTS["compact"], n, str(files[n]))
        for n in pages:
            side = json.loads(files[n].with_suffix(".json").read_text(encoding="utf-8"))
            report += [tuple(r) for r in side["report"]]
            placelog.RECORDS.extend(side["records"])
            w.add_page(PdfReader(str(files[n])).pages[0])
            stats.append(f"{n}:{side['cnt']}")
        keep = {f.name for f in files.values()} | {f.with_suffix(".json").name for f in files.values()}
        for f in cdir.glob("p*_*.*"):                      # drop stale versions of the pages just built
            if f.name not in keep and int(f.name[1:3]) in files:
                f.unlink()
        stats.append(f"[{len(todo)} rebuilt, {len(pages) - len(todo)} cached, {time.time() - t0:.0f}s]")
    with open(out_pdf, "wb") as fh:
        w.write(fh)
    from drawtrans.render import placelog
    placelog.dump(Path(out_pdf).with_suffix(".placements.json"), {"route": "scan", "pages": list(pages)})
    placelog.reset()
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
    if not C.i18n.pair().default:                       # target label + source label of the language pair
        lab = C.i18n.toc_labels()
        cols = [(lab[1], 34), (lab[2], 150), (lab[3], PW - L - R - 236), (lab[4], 52)]
    xs = [L]
    for _, cw in cols:
        xs.append(xs[-1] + cw)
    buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=A4)
    links, state = [], {"y": 0, "page": 0}

    def header(first):
        y = PH - TOP
        if first:
            c.setFont(F, 15); c.setFillColorRGB(0, 0, 0); pr = C.i18n.pair()
            c.drawCentredString(PW / 2, y - 14, "目  录" if pr.default else C.i18n.TOC.get(pr.tgt.code, C.i18n.TOC["en"])[0])
            c.setFont(F, 9.5)
            c.drawCentredString(PW / 2, y - 30, "Содержание / Contents" if pr.default else C.i18n.TOC.get(pr.src.code, C.i18n.TOC["en"])[0])
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


def output_opts(config_path, args):
    """CLI flags win; otherwise project.toml [output] appendix / compact."""
    import tomllib
    p = Path(config_path or "project.toml").resolve()
    t = {}
    if p.exists():
        t = tomllib.loads(p.read_text(encoding="utf-8")).get("output", {})
    return bool(args.appendix or t.get("appendix", False)), bool(args.compact or t.get("compact", False))


def add_appendix(ctx, final_pdf, pages):
    from drawtrans.render import appendix
    hits = appendix.detect(appendix.collect_texts(ctx, pages))
    tmp = Path(str(final_pdf) + ".tmp")
    n = appendix.append_to_pdf(final_pdf, tmp, hits, ctx.cfg.code)
    tmp.replace(final_pdf)
    print(f"   + appendix: {len(hits)} reference row(s), {n} page(s)" if n else "   (appendix: no known grades / standards found)")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--pages")
    ap.add_argument("--no-toc", action="store_true")
    ap.add_argument("--appendix", action="store_true", help="add the RU->CN reference appendix (仅供参考)")
    ap.add_argument("--compact", action="store_true", help="精简版: no 根/间距/件 count labels")
    a = ap.parse_args()
    with_appendix, engine.OPTS["compact"] = output_opts(a.config, a)
    cfg = C.load(a.config)
    ctx = C.Ctx(cfg); ctx.mkdirs(); engine.init(ctx)
    conf = Path(a.config or "project.toml").resolve()
    n = pymupdf.open(cfg.pdf).page_count
    if a.pages:
        pages = pagesel.expand(a.pages)
        build(ctx, pages, Path(cfg.output) / f"{cfg.code}_test.pdf", ctx.w / "review" / "build_report_test.tsv", config=conf)
        return
    body = Path(cfg.output) / f"{cfg.code}_body.pdf"
    rep = build(ctx, list(range(1, n + 1)), body, ctx.w / "review" / "build_report.tsv", config=conf)
    final = Path(cfg.output) / f"{cfg.code}_中文对照版.pdf"
    if a.no_toc:
        body.replace(final)
    else:
        k = add_toc(ctx, body, final); body.unlink()
        print(f"   + {k} contents page(s) -> {final}")
    if with_appendix:
        add_appendix(ctx, final, list(range(1, n + 1)))
    lost = [r for r in rep if r[2] in ("未放置",)]
    if lost:
        print(f"!! {len(lost)} label(s) could not be placed — see build_report.tsv")


if __name__ == "__main__":
    main()
