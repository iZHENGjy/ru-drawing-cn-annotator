"""Render a page (or a clip of it) of a PDF to PNG for visual QA.
usage: 30_preview.py <pdf> <page_index_0based> <out.png> [x0,y0,x1,y1 fraction clip | -] [dpi]"""
import sys
import pymupdf

if len(sys.argv) < 4:
    sys.exit(__doc__)
pdf, idx, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
doc = pymupdf.open(pdf)
p = doc[idx]
clip = None
if len(sys.argv) > 4 and sys.argv[4] != "-":
    f = [float(v) for v in sys.argv[4].split(",")]
    r = p.rect
    clip = pymupdf.Rect(r.x0 + f[0] * r.width, r.y0 + f[1] * r.height, r.x0 + f[2] * r.width, r.y0 + f[3] * r.height)
dpi = int(sys.argv[5]) if len(sys.argv) > 5 else 150
p.get_pixmap(clip=clip, dpi=dpi).save(out)
