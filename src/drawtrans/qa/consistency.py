"""Terminology consistency of a whole volume: the same source text translated in more than one way.

    python scripts/qa/consistency.py [--config project.toml] [--tsv out.tsv]

Reads the final maps (work/map/pNN.json + .fix.json, line texts from work/lines) and the page notes,
groups the source texts by their TM key (numbers as ⟨n⟩, OCR look-alikes folded) and lists every key
with two or more different translations (numbers in the translations are masked before comparing).
Report only (an advisory gate of ``rudraw run``): a person decides which wording is right and fixes it
once in the TM with ``src = human``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path



def _mask(z: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"\d+(?:[.,]\d+)?", "#", z or ""))


def collect(ctx) -> dict:
    """{key: {masked zh: [(page, source text, zh)]}}"""
    from drawtrans.config import scanroute as C
    from drawtrans.segment import segments as S
    from drawtrans.memory import tm as T
    out: dict = defaultdict(lambda: defaultdict(list))

    def add(pg, src, zh):
        if not src or not zh or not zh.strip():
            return
        key = S.fold(T.make_key(S.norm(src))[0])
        if len(re.sub(r"[^\w]", "", key)) < 3:
            return
        out[key][_mask(zh)].append((pg, src, zh))
    for mf in sorted(Path(ctx.w / "map").glob("p??.json")):
        pg = int(mf.stem[1:])
        m = C.load_map(ctx, pg)
        texts = {}
        lf = ctx.lines(pg)
        if lf.exists():
            for ln in lf.read_text(encoding="utf-8").splitlines()[1:]:
                f = ln.split("\t")
                if len(f) >= 7:
                    texts[f[0]] = f[6]
        for k, zh in (m.get("lines") or {}).items():
            add(pg, texts.get(str(k), ""), zh)
        for e in m.get("extra") or []:
            add(pg, e.get("ru") or "", e.get("zh") or "")
        for q in m.get("paras") or []:
            add(pg, q.get("ru") or "", q.get("zh") or "")
    return out


def report(groups: dict) -> list[dict]:
    rows = []
    for key, variants in groups.items():
        if len(variants) < 2:
            continue
        vs = sorted(variants.values(), key=len, reverse=True)
        rows.append({"key": key, "n_variants": len(vs),
                     "variants": [{"zh": v[0][2], "n": len(v), "pages": sorted({p for p, _, _ in v})[:12],
                                   "example": v[0][1]} for v in vs]})
    rows.sort(key=lambda r: -sum(v["n"] for v in r["variants"]))
    return rows


def write_tsv(rows, path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("source (example)\tvariants\ttranslation\toccurrences\tpages\n")
        for r in rows:
            for v in r["variants"]:
                fh.write(f"{v['example']}\t{r['n_variants']}\t{v['zh']}\t{v['n']}\t{','.join(map(str, v['pages']))}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--tsv")
    a = ap.parse_args()
    from drawtrans.config import scanroute as C
    ctx = C.Ctx(C.load(a.config))
    rows = report(collect(ctx))
    out = Path(a.tsv or ctx.w / "review" / "consistency.tsv")
    out.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(rows, out)
    print(f"inconsistent terms: {len(rows)} -> {out}")
    for r in rows[:15]:
        print("  " + r["variants"][0]["example"][:40] + "  ->  " + "  |  ".join(f"{v['zh']} ({v['n']})" for v in r["variants"][:3]))


if __name__ == "__main__":
    main()
