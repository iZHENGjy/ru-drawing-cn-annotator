"""Render a WCS window of a translated DXF to PNG: line art grey, original text black,
Chinese (layer CN_TRANSLATION) blue.
usage: cad/20_preview.py <translated.dxf> <out.png> <x0> <y0> <x1> <y1> [dpi]"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.collections import LineCollection
from ezdxf import recover
from collect import collect
from engine import place

if len(sys.argv) < 7:
    sys.exit(__doc__)
if place.FONT_FILE:
    font_manager.fontManager.addfont(place.FONT_FILE)
    plt.rcParams["font.family"] = font_manager.FontProperties(fname=place.FONT_FILE).get_name()
src, out, x0, y0, x1, y1 = sys.argv[1], sys.argv[2], *map(float, sys.argv[3:7])
dpi = int(sys.argv[7]) if len(sys.argv) > 7 else 150
doc, _ = recover.readfile(src)

segs = []


def walk(ents, depth):
    for e in ents:
        t = e.dxftype()
        try:
            if t == "LINE":
                segs.append([(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)])
            elif t == "LWPOLYLINE":
                p = [(q[0], q[1]) for q in e.get_points()]
                if e.closed: p.append(p[0])
                segs.extend([[a, b] for a, b in zip(p, p[1:])])
            elif t == "INSERT" and depth < 8:
                walk(list(e.virtual_entities()), depth + 1)
        except Exception:
            pass


walk(doc.modelspace(), 0)
segs = [s for s in segs if (min(s[0][0], s[1][0]) < x1 and max(s[0][0], s[1][0]) > x0
                            and min(s[0][1], s[1][1]) < y1 and max(s[0][1], s[1][1]) > y0)]
items = [i for i in collect(doc) if x0 <= i.x <= x1 and y0 <= i.y <= y1]
W = 16; H = W * (y1 - y0) / (x1 - x0)
fig = plt.figure(figsize=(W, H), dpi=dpi); ax = fig.add_axes([0, 0, 1, 1])
ax.add_collection(LineCollection(segs, colors="#b0b0b0", linewidths=0.4))
pt_per_unit = W * 72 / (x1 - x0)
for i in items:
    cn = i.layer == "CN_TRANSLATION"
    ax.text(i.x, i.y, i.text, rotation=i.rot, rotation_mode="anchor", ha="left", va="baseline",
            fontsize=max(1.0, i.h * pt_per_unit * (1.0 if cn else 1.1)), color="#0040c0" if cn else "black")
ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal"); ax.axis("off")
fig.savefig(out, facecolor="white")
print(len(segs), "segments", len(items), "texts")
