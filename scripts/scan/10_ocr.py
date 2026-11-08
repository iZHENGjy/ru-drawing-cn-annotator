"""Step 1 — render each page at 300 dpi, run Tesseract (rus+eng, psm 11), build numbered lines.

    python scripts/scan/10_ocr.py [--config project.toml] [--pages 1-5,9] [--threads 6]

Writes work/png/pNN.png, work/ocr/pNN.tsv (300 dpi pixel coordinates, visual orientation)
and work/lines/pNN.tsv (the numbered lines the translator maps).  Existing OCR is reused;
delete work/ocr/pNN.tsv to redo a page.  Use tessdata_best (rus) for small drawing text.
"""
import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf  # noqa: E402
from scan import ctx as C  # noqa: E402


def pages_arg(s, n):
    if not s:
        return list(range(1, n + 1))
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return [p for p in out if 1 <= p <= n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--pages")
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    a = ap.parse_args()
    cfg = C.load(a.config)
    ctx = C.Ctx(cfg); ctx.mkdirs()
    tess = C.find_tesseract(cfg)
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    if cfg.tessdata:
        env["TESSDATA_PREFIX"] = str(C._p(cfg.root, cfg.tessdata))
    doc = pymupdf.open(cfg.pdf)
    pages = pages_arg(a.pages, doc.page_count)

    def run(pg):
        page = pymupdf.open(cfg.pdf)[pg - 1]
        r = page.rect
        if not ctx.ocr(pg).exists() or ctx.ocr(pg).stat().st_size < 2000:
            dpi = 300 if r.width * r.height * (300 / 72) ** 2 <= 9e7 else 150   # Tesseract image limit
            page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY).save(ctx.png(pg))
            base = ctx.ocr(pg).with_suffix("")
            raw = Path(str(base) + "_raw")
            subprocess.run([tess, str(ctx.png(pg)), str(raw), "-l", cfg.lang, "--psm", "11", "tsv"], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3600, check=False)
            rawtsv = Path(str(raw) + ".tsv")
            if not rawtsv.exists():
                return f"p{pg:02d}: tesseract produced no TSV (check tessdata / configs folder)"
            f = 300 / dpi
            rows = rawtsv.read_text(encoding="utf-8").split("\n")
            with open(ctx.ocr(pg), "w", encoding="utf-8", newline="") as fo:
                fo.write(rows[0] + "\n")
                for ln in rows[1:]:
                    c = ln.split("\t")
                    if len(c) < 12:
                        continue
                    try:
                        for k in (6, 7, 8, 9):
                            c[k] = str(int(round(int(c[k]) * f)))
                    except ValueError:
                        continue
                    fo.write("\t".join(c) + "\n")
            rawtsv.unlink()
            if dpi != 300:   # keep a 300 dpi render for review crops / second detector
                page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY).save(ctx.png(pg))
        lines = C.build_lines(ctx.ocr(pg))
        C.write_lines(ctx.lines(pg), lines, r.width, r.height)
        cyr = sum(1 for d in lines if len(C.CYR.findall(d["text"])) >= 2)
        return f"p{pg:02d}: {len(lines)} lines, {cyr} Russian"

    with ThreadPoolExecutor(a.threads) as ex:
        for m in ex.map(run, pages):
            print(m, flush=True)


if __name__ == "__main__":
    main()
