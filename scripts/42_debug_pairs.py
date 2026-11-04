"""List unpaired multi-line RU runs and EN-only runs of given pages (pairing diagnostics).
usage: 42_debug_pairs.py <pages: 3,7> [--config project.toml] [--pdf in.pdf]"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pymupdf
from engine import config
from engine.layout import analyse
from engine.place import pair_runs

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("pages")
ap.add_argument("--config", default=None)
ap.add_argument("--pdf", default=None)
a = ap.parse_args()
sys.stdout.reconfigure(encoding="utf-8")
cfg = config.load(a.config)
config.apply(cfg)
doc = pymupdf.open(a.pdf or cfg.pdf)
for pn in map(int, a.pages.split(",")):
    p = doc[pn - 1]; p.remove_rotation()
    runs, words, other, segs = analyse(p)
    print(f"=== p{pn}")
    for u in pair_runs(runs):
        r = u.ru if u.mode == "insert" else (u.en if u.mode == "en_only" else None)
        if r is None or len(r.lines) < 2:
            continue
        print(f"  [{u.mode}] {[round(v) for v in r.frect]} s{r.size:.1f} lines={len(r.lines)} | {r.text.replace(chr(10), ' / ')[:150]}")
