"""Which blocks are actually used (directly or nested from modelspace / layouts), and what
translatable text each contains.  Also prints the unique RU / EN / ZH strings.
Needs <review>/cad_texts.csv from cad/01_census.py.
usage: cad/02_blocks.py [--config project.toml] [--dxf in.dxf]"""
import argparse, csv, sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ezdxf import recover
from engine import config

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--config", default=None)
ap.add_argument("--dxf", default=None)
a = ap.parse_args()
sys.stdout.reconfigure(encoding="utf-8")
cfg = config.load(a.config)
doc, _ = recover.readfile(a.dxf or cfg.dxf)

uses = Counter()


def visit(layout, depth=0):
    for e in layout:
        if e.dxftype() == "INSERT":
            uses[e.dxf.name] += 1
            if depth < 6 and e.dxf.name in doc.blocks:
                visit(doc.blocks[e.dxf.name], depth + 1)
        elif e.dxftype() == "DIMENSION" and e.dxf.get("geometry"):
            uses[e.dxf.geometry] += 1


visit(doc.modelspace())
for l in doc.layouts:
    if l.name != "Model":
        visit(l)
print("layouts:", [l.name for l in doc.layouts])

rows = list(csv.DictReader(open(cfg.review / "cad_texts.csv", encoding="utf-8-sig")))
per = defaultdict(Counter)
for r in rows:
    per[r["owner"]][r["lang"]] += 1
print("\nblocks with ru/en text  (uses):")
for owner, c in sorted(per.items(), key=lambda kv: -(kv[1]["ru"] + kv[1]["en"])):
    if owner == "MODEL" or not (c["ru"] or c["en"] or c["zh"]):
        continue
    name = owner[6:]
    print(f"  {name[:30]:30} uses={uses.get(name, 0):4}  ru={c['ru']:3} en={c['en']:3} zh={c['zh']:3}")
for lg in ("ru", "en", "zh"):
    u = Counter(r["text"] for r in rows if r["lang"] == lg and (r["owner"] == "MODEL" or uses.get(r["owner"][6:], 0)))
    print(f"\n=== {lg}: {len(u)} unique (in used blocks / model)")
    for t, n in u.most_common():
        print(f"{n:4}  {t[:110]}")
