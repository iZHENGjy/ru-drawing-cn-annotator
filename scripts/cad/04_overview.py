"""Quick overview PNG of a DXF: modelspace LINE/LWPOLYLINE geometry in grey, text anchors
coloured by language (red = Cyrillic, blue = Latin, green = garbled/other).
usage: cad/04_overview.py <in.dxf> <out.png>"""
import re, sys
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from ezdxf import recover

if len(sys.argv) < 3:
    sys.exit(__doc__)
doc, _ = recover.readfile(sys.argv[1])
msp = doc.modelspace()
segs = []
for e in msp.query("LINE"):
    segs.append([(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)])
for e in msp.query("LWPOLYLINE"):
    p = [(x, y) for x, y, *_ in e.get_points()]
    segs += [[p[i], p[i + 1]] for i in range(len(p) - 1)]
txt = []
for e in msp.query("TEXT MTEXT"):
    s = e.dxf.text if e.dxftype() == "TEXT" else e.plain_text()
    c = "red" if re.search("[А-Яа-яЁё]", s) else ("blue" if re.search("[A-Za-z0-9]", s) else "green")
    txt.append((e.dxf.insert.x, e.dxf.insert.y, s, c))
if not txt:
    sys.exit("no TEXT/MTEXT in modelspace")
pts = np.array([(x, y) for x, y, *_ in txt])
lo, hi = np.percentile(pts, 1, axis=0), np.percentile(pts, 99, axis=0)
span = np.maximum(hi - lo, 1e-6)
pad = span * 0.05
fig = plt.figure(figsize=(22, max(4, 22 * span[1] / span[0])), dpi=100)
ax = fig.add_axes([0, 0, 1, 1])
ax.add_collection(LineCollection(segs, colors="#999999", linewidths=0.3))
for x, y, s, c in txt:
    ax.text(x, y, s[:40], color=c, fontsize=4)
ax.set_xlim(lo[0] - pad[0], hi[0] + pad[0]); ax.set_ylim(lo[1] - pad[1], hi[1] + pad[1])
ax.set_aspect("equal"); ax.axis("off")
fig.savefig(sys.argv[2], facecolor="white")
print("segments", len(segs), "texts", len(txt))
