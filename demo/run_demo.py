"""Run the whole demo and refresh the README images.

    python demo/run_demo.py

1. regenerate demo_drawing.pdf (synthetic, make_demo_pdf.py)
2. sheet 1 (Russian only)   -> 20_build.py --english keep     (original untouched)
3. sheet 2 (bilingual RU/EN)-> 20_build.py --english replace  (English swapped for Chinese)
4. render before/after PNGs to docs/img/
5. DXF demo: make_demo_dxf.py -> cad/10_build.py -> cad/20_preview.py

The translation memory (demo/tm.json) was built with the normal loop:
20_build.py --dry -> 41_export_todo.py -> translations.tsv -> 40_merge_tm.py.
"""
import subprocess, sys
from pathlib import Path
import pymupdf

DEMO = Path(__file__).resolve().parent
ROOT = DEMO.parent
S = ROOT / "scripts"
IMG = ROOT / "docs" / "img"
OUT = DEMO / "work" / "output"
py = sys.executable


def run(*args):
    print(">", " ".join(str(a) for a in args))
    subprocess.run([py, *map(str, args)], cwd=DEMO, check=True)


def png(pdf, page, out, clip=None, dpi=110):
    p = pymupdf.open(pdf)[page]
    r = p.rect
    c = None if clip is None else pymupdf.Rect(r.x0 + clip[0] * r.width, r.y0 + clip[1] * r.height,
                                               r.x0 + clip[2] * r.width, r.y0 + clip[3] * r.height)
    p.get_pixmap(clip=c, dpi=dpi).save(out)
    print("  ", out.relative_to(ROOT))


run(DEMO / "make_demo_pdf.py")
run(S / "20_build.py", "1", "--out", "demo_sheet1_CN.pdf")
run(S / "20_build.py", "2", "--english", "replace", "--out", "demo_sheet2_CN.pdf")
IMG.mkdir(parents=True, exist_ok=True)
src = DEMO / "demo_drawing.pdf"
s1, s2 = OUT / "demo_sheet1_CN.pdf", OUT / "demo_sheet2_CN.pdf"
png(src, 0, IMG / "sheet1_before.png", dpi=72)
png(s1, 0, IMG / "sheet1_after.png", dpi=72)
detail = (0.56, 0.04, 0.99, 0.76)          # notes + specification table
png(src, 0, IMG / "sheet1_detail_before.png", detail, dpi=130)
png(s1, 0, IMG / "sheet1_detail_after.png", detail, dpi=130)
notes = (0.05, 0.05, 0.52, 0.60)           # bilingual list of drawings + general notes
png(src, 1, IMG / "sheet2_detail_before.png", notes, dpi=120)
png(s2, 0, IMG / "sheet2_detail_after.png", notes, dpi=120)

run(DEMO / "make_demo_dxf.py")
run(S / "cad" / "10_build.py", "--dxf", "demo_plan.dxf")
run(S / "cad" / "20_preview.py", OUT / "demo_plan_CN.dxf", IMG / "dxf_after.png", -500, -1500, 9500, 6500, 110)
