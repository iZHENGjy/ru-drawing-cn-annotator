"""Helper for translators — render a page region with the numbered OCR line boxes drawn on it.

    python scripts/scan/12_preview.py PAGE [--box x0 y0 x1 y1] [--dpi 200] [--out img.png] [--config ...]

--box is in page fractions (0..1, top-left origin).  Red = line with Russian (must appear in
the map), green = other OCR lines.  The number above each box is the line id used in
map/pNN.json.  Keep the long side of one image <= ~2500 px: preview big sheets in parts.
Without --box a coarse whole-page overview is written.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from scan import ctx as C  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page", type=int)
    ap.add_argument("--box", type=float, nargs=4, default=[0, 0, 1, 1])
    ap.add_argument("--dpi", type=float)
    ap.add_argument("--out")
    ap.add_argument("--config")
    a = ap.parse_args()
    cfg = C.load(a.config); ctx = C.Ctx(cfg)
    p = pymupdf.open(cfg.pdf)[a.page - 1]; r = p.rect
    x0f, y0f, x1f, y1f = a.box
    clip = pymupdf.Rect(x0f * r.width, y0f * r.height, x1f * r.width, y1f * r.height)
    dpi = a.dpi or min(220, 2400 / max(clip.width, clip.height) * 72)
    z = dpi / 72
    vis = clip * p.derotation_matrix if p.rotation else clip
    pix = p.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=vis, colorspace=pymupdf.csRGB)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    dr = ImageDraw.Draw(im)
    font = ImageFont.truetype(C.font_file(cfg), max(11, int(dpi / 9)))
    for L in C.load_lines(ctx, a.page, r.width, r.height):
        if L["x1"] < clip.x0 or L["x0"] > clip.x1 or L["y1"] < clip.y0 or L["y0"] > clip.y1:
            continue
        bx = [(L["x0"] - clip.x0) * z, (L["y0"] - clip.y0) * z, (L["x1"] - clip.x0) * z, (L["y1"] - clip.y0) * z]
        col = (220, 0, 0) if len(C.CYR.findall(L["text"])) >= 2 else (0, 140, 0)
        dr.rectangle(bx, outline=col, width=2)
        dr.text((bx[0], max(0, bx[1] - dpi / 9 - 2)), str(L["id"]), fill=col, font=font)
    out = a.out or str(ctx.w / f"preview_p{a.page:02d}.png")
    im.save(out)
    print(out, im.size)


if __name__ == "__main__":
    main()
