"""Output-standard gate: the rules of docs/output_standard.md checked by code.

    python scripts/qa/standard.py OUT.placements.json [--json result.json]   # exit 1 = not deliverable

Input is the placement record the engines write next to the output PDF (``*.placements.json``,
see ``scripts/placelog.py``).  Rules (only records made under the standard count, i.e. ``std >= 2``;
records of the approved legacy layout (``std = 1``) are listed as advisory):

  above            a label is set above its source text (it must be below, or beside with a leader)
  split_paragraph  a paragraph of the extension column ends mid-sentence and the next one continues it
  title_block_cell a title-block field is translated inside its cell instead of the legend line
  row_number       a table row of the extension column has no row number while the table is numbered
  font_floor       column text below 6.4 pt, labels below 4.0 pt
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from drawtrans import languages as i18n  # noqa: E402

COLUMN_FLOOR = 6.4
LABEL_FLOOR = 4.0
SENT_END = re.compile(r"[.!?:;。！？：；…)\]»\"”]\s*$")
RULES = ("above", "split_paragraph", "title_block_cell", "row_number", "font_floor")


def _starts_lower(t: str) -> bool:
    t = (t or "").lstrip(" «\"'(-–—")
    if not t or i18n.pair().src.script == "hani":
        return False
    return t[0].islower()


def check(items: list[dict]) -> dict:
    """-> {"violations": [...], "advisory": [...], "counts": {rule: n}, "ok": bool}"""
    viol, adv = [], []

    def hit(rule, it, why):
        (viol if (it.get("std") or 1) >= 2 else adv).append({"rule": rule, "pg": it.get("pg"), "text": (it.get("text") or "")[:60],
                                                             "why": why})
    by_pg = defaultdict(list)
    for it in items:
        by_pg[it.get("pg")].append(it)
    for pg, its in by_pg.items():
        for it in its:
            k = it.get("kind")
            if k == "label":
                bb, src = it.get("bb"), it.get("src")
                if bb and src and not it.get("vertical"):
                    xov = min(bb[2], src[2]) - max(bb[0], src[0])
                    if bb[3] <= src[1] + 0.5 and xov > 0:
                        hit("above", it, f"label bottom {bb[3]} above source top {src[1]}")
                if it.get("tb"):
                    hit("title_block_cell", it, "title-block field translated in its cell")
                if it.get("fs") is not None and it["fs"] < LABEL_FLOOR:
                    hit("font_floor", it, f"label {it['fs']} pt < {LABEL_FLOOR}")
            elif k in ("column", "block") and it.get("fs") is not None and it["fs"] < COLUMN_FLOOR:
                hit("font_floor", it, f"column text {it['fs']} pt < {COLUMN_FLOOR}")
        paras = [it for it in its if it.get("kind") == "para" and it.get("ru")]
        for a, b in zip(paras, paras[1:]):
            if not SENT_END.search(a["ru"].strip()) and _starts_lower(b["ru"]):
                hit("split_paragraph", b, f"continues «{a['ru'][-30:]}»")
        rows = sorted((it for it in its if it.get("kind") == "row"), key=lambda r: r.get("y") or 0)
        numbered = [bool(re.match(r"^\d{1,3}\.\s", r.get("text") or "")) for r in rows]
        if sum(numbered) >= 2:
            for r, n in list(zip(rows, numbered))[1:]:             # the first row is the header
                if not n:
                    hit("row_number", r, "table row without its row number")
    counts = {r: sum(1 for v in viol if v["rule"] == r) for r in RULES}
    return {"ok": not viol, "violations": viol, "advisory": adv[:200], "advisory_n": len(adv), "counts": counts}


def check_file(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"ok": True, "violations": [], "advisory": [], "advisory_n": 0, "counts": {}, "missing": str(p)}
    d = json.loads(p.read_text(encoding="utf-8"))
    return check(d.get("items") or [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("placements")
    ap.add_argument("--json")
    a = ap.parse_args()
    r = check_file(a.placements)
    for v in r["violations"][:40]:
        print(f"p{v['pg']}  {v['rule']:17s} {v['text']}  ({v['why']})")
    print(f"standard: {'OK' if r['ok'] else 'NOT DELIVERABLE'}  " + "  ".join(f"{k}={v}" for k, v in r["counts"].items())
          + f"  advisory={r['advisory_n']}")
    if a.json:
        Path(a.json).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.exit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()
