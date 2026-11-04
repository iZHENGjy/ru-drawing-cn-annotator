"""Census of all text in a DXF (modelspace + block definitions).
Writes <review>/cad_texts.csv: handle, owner, type, layer, style, height, rotation, x, y, lang, text.
usage: cad/01_census.py [--config project.toml] [--dxf in.dxf]"""
import argparse, csv, re, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ezdxf import recover
from engine import config

CYR = re.compile(r"[А-Яа-яЁё]"); LAT = re.compile(r"[A-Za-z]{2,}"); HAN = re.compile(r"[一-鿿]")
BAD = re.compile(r"[\u0080-ÿ�]")


def lang(t):
    if HAN.search(t): return "zh"
    if CYR.search(t): return "ru"
    if BAD.search(t): return "garbled"
    if LAT.search(t): return "en"
    return "neu"


def walk(doc):
    for e in doc.modelspace():
        yield "MODEL", e
    for b in doc.blocks:
        if b.name.lower().startswith("*model"):
            continue
        for e in b:
            yield "BLOCK:" + b.name, e


def texts(e):
    t = e.dxftype()
    if t == "TEXT": yield e, e.dxf.text
    elif t == "MTEXT": yield e, e.plain_text()
    elif t in ("ATTRIB", "ATTDEF"): yield e, e.dxf.text
    elif t == "INSERT":
        for a in e.attribs: yield a, a.dxf.text


ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--config", default=None)
ap.add_argument("--dxf", default=None)
a = ap.parse_args()
sys.stdout.reconfigure(encoding="utf-8")
cfg = config.load(a.config)
doc, aud = recover.readfile(a.dxf or cfg.dxf)
print("codepage", doc.header.get("$DWGCODEPAGE"), "version", doc.dxfversion, "insunits", doc.header.get("$INSUNITS"))
rows, cnt = [], Counter()
for owner, e in walk(doc):
    for te, s in texts(e):
        if not s or not s.strip():
            continue
        ins = te.dxf.get("insert", (0, 0, 0))
        h = (te.dxf.get("char_height", 0) if te.dxftype() == "MTEXT" else te.dxf.get("height", 0)) or 0
        rot = te.dxf.get("rotation", 0) or 0
        lg = lang(s); cnt[(owner == "MODEL", lg)] += 1
        rows.append([te.dxf.handle, owner, te.dxftype(), te.dxf.layer, te.dxf.get("style", ""), round(h, 3),
                     round(rot, 1), round(ins[0], 2), round(ins[1], 2), lg, s.replace("\n", "\\n")])
cfg.review.mkdir(parents=True, exist_ok=True)
with open(cfg.review / "cad_texts.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["handle", "owner", "type", "layer", "style", "height", "rot", "x", "y", "lang", "text"])
    w.writerows(rows)
print("texts", len(rows))
for k, v in sorted(cnt.items()):
    print(k, v)
print("--- garbled samples (wrong code page? see collect.fix_mojibake)")
for r in [r for r in rows if r[9] == "garbled"][:12]:
    print(r[1][:20], r[2], r[3], repr(r[10][:60]))
