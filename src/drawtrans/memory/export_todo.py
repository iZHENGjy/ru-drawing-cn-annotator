"""Export <review>/tm_todo.json as numbered lines (index, key, first pages) for translation.
usage: 41_export_todo.py [--config project.toml]   ->  <review>/todo_all.txt"""
import argparse
import json
import sys

from drawtrans.config import textroute as config

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--config", default=None)
a = ap.parse_args()
sys.stdout.reconfigure(encoding="utf-8")
cfg = config.load(a.config)
d = json.loads((cfg.review / "tm_todo.json").read_text(encoding="utf-8"))
out = [f"{i}\t{k}\tp{','.join(map(str, v['pages'][:4]))}" for i, (k, v) in enumerate(d.items())]
(cfg.review / "todo_all.txt").write_text("\n".join(out), encoding="utf-8")
print(len(out), "->", cfg.review / "todo_all.txt")
