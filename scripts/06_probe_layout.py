"""Probe: dump the text lines of given pages with bbox, font size, direction and language tag.
usage: 06_probe_layout.py <pages: 1,3> [max_lines] [--config project.toml] [--pdf in.pdf]"""
import argparse, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pymupdf
from engine import config

CYR = re.compile(r"[А-Яа-яЁё]"); LAT = re.compile(r"[A-Za-z]{2,}")


def tag(t):
    c, l = bool(CYR.search(t)), bool(LAT.search(t))
    return "RU+EN" if c and l else ("RU" if c else ("EN" if l else "--"))


ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("pages")
ap.add_argument("limit", nargs="?", type=int, default=120)
ap.add_argument("--config", default=None)
ap.add_argument("--pdf", default=None)
a = ap.parse_args()
sys.stdout.reconfigure(encoding="utf-8")
doc = pymupdf.open(a.pdf or config.load(a.config).pdf)
for pn in map(int, a.pages.split(",")):
    page = doc[pn - 1]
    print(f"=== p{pn} rot={page.rotation} rect={page.rect}")
    d = page.get_text("dict")
    rows = []
    for b in d["blocks"]:
        for l in b.get("lines", []):
            t = "".join(s["text"] for s in l["spans"]).strip()
            if not t:
                continue
            x0, y0, x1, y1 = pymupdf.Rect(l["bbox"]) * page.rotation_matrix  # visual coords
            rows.append((round(y0), round(x0), round(x1), round(y1), round(l["spans"][0]["size"], 1),
                         tuple(round(v, 2) for v in l["dir"]), tag(t), t))
    for r in sorted(rows)[:a.limit]:
        print(r)
    print("lines", len(rows), "dirs", {r[5] for r in rows})
