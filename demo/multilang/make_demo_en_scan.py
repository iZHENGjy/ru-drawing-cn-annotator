"""Synthetic English drawing as an image-only PDF (a "scan"), for the en -> zh scan-route demo.

Everything is invented (no project data).  usage: python demo/multilang/make_demo_en_scan.py [out.pdf]
"""
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("en_scan.pdf")
FONT = next((f for f in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                          "/Library/Fonts/Arial.ttf") if Path(f).is_file()), None)
W, H = 3508, 2480                       # A4 landscape at 300 dpi
im = Image.new("L", (W, H), 255)
d = ImageDraw.Draw(im)
f = lambda s: ImageFont.truetype(FONT, s)   # noqa: E731
d.rectangle((60, 60, W - 60, H - 60), outline=0, width=6)
# plan of a column base
d.rectangle((500, 450, 1300, 1250), outline=0, width=5)            # base plate
d.rectangle((800, 750, 1000, 950), outline=0, width=4)             # column
for x, y in ((600, 550), (1200, 550), (600, 1150), (1200, 1150)):
    d.ellipse((x - 30, y - 30, x + 30, y + 30), outline=0, width=4)
d.text((500, 330), "PLAN OF COLUMN BASE", font=f(70), fill=0)
d.text((1380, 520), "BASE PLATE PL-30", font=f(52), fill=0)
d.text((1380, 640), "ANCHOR BOLT M24", font=f(52), fill=0)
d.text((1380, 760), "STEEL COLUMN", font=f(52), fill=0)
d.text((1380, 880), "NON-SHRINK GROUT", font=f(52), fill=0)
d.line((1000, 850, 1370, 790), fill=0, width=3)
# notes
d.text((2200, 330), "GENERAL NOTES", font=f(64), fill=0)
notes = ["1. All dimensions are in millimetres.",
         "2. Concrete for the pedestal shall be grade C30.",
         "3. Anchor bolts shall be hot-dip galvanized.",
         "4. Grout the base plate after final alignment."]
for i, t in enumerate(notes):
    d.text((2200, 470 + i * 110), t, font=f(46), fill=0)
# title block
x0, y0 = 2000, 1900
d.rectangle((x0, y0, W - 80, H - 80), outline=0, width=5)
for k, (lab, val) in enumerate((("Drawn", "A. Smith"), ("Checked", "B. Jones"), ("Scale", "1:20"), ("Sheet", "1 of 1"))):
    y = y0 + 30 + k * 110
    d.text((x0 + 40, y), lab, font=f(46), fill=0)
    d.text((x0 + 420, y), val, font=f(46), fill=0)
d.text((x0 + 800, y0 + 60), "Title: COLUMN BASE DETAIL", font=f(52), fill=0)
d.text((x0 + 800, y0 + 200), "Drawing No. DEMO-S-001", font=f(46), fill=0)
doc = pymupdf.open()
page = doc.new_page(width=842, height=595)
import io
buf = io.BytesIO(); im.save(buf, format="PNG")
page.insert_image(page.rect, stream=buf.getvalue())
doc.save(OUT, deflate=True)
print("wrote", OUT)
