"""Dump every visible text item of modelspace in WCS (blocks exploded) -> <review>/cad_wcs.csv.
usage: cad/03_dump_wcs.py [--config project.toml] [--dxf in.dxf]"""
import argparse, csv, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ezdxf import recover
from collect import collect
from engine import config

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--config", default=None)
ap.add_argument("--dxf", default=None)
a = ap.parse_args()
sys.stdout.reconfigure(encoding="utf-8")
cfg = config.load(a.config)
doc, _ = recover.readfile(a.dxf or cfg.dxf)
items = collect(doc)
print("items", len(items), Counter(i.lang for i in items))
cfg.review.mkdir(parents=True, exist_ok=True)
with open(cfg.review / "cad_wcs.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["lang", "h", "rot", "x", "y", "layer", "path", "text", "raw"])
    for i in sorted(items, key=lambda i: (-round(i.y, 0), i.x)):
        w.writerow([i.lang, round(i.h, 3), round(i.rot, 1), round(i.x, 2), round(i.y, 2), i.layer, i.path,
                    i.text, i.raw if i.raw != i.text else ""])
for i in items:
    if i.raw != i.text and i.lang == "ru":
        print("FIXED mojibake:", repr(i.raw[:40]), "->", i.text[:60])
hs = Counter(round(i.h, 2) for i in items if i.lang in ("en", "ru"))
print("heights en/ru:", hs.most_common(12))
