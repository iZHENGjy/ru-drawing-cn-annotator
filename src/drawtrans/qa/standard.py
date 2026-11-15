"""Output-standard gate: the rules of docs/output_standard.md checked by code.

    python scripts/qa/standard.py OUT.placements.json [--json result.json]   # exit 1 = not deliverable

Input is the placement record the engines write next to the output PDF (``*.placements.json``,
see ``drawtrans.render.placelog``).  Rules (only records made under the standard count, i.e. ``std >= 2``;
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
from drawtrans.layout.policy import V2  # noqa: E402

COLUMN_FLOOR = 6.4                      # output standard 2 (PDF pt)
LABEL_FLOOR = 4.0
# output standard 3: printed sizes and leader limits come from layout.policy (the engine's numbers)
LEADER_MAX_SMALL, LEADER_MAX_LARGE = V2.leader_max_small, V2.leader_max_large
LEADERS_PER_PAGE, LEADER_MEAN_MAX, LEADER_SHARE_MAX = V2.leaders_per_page, V2.leader_mean_max, V2.leader_share_max


def policy_floor(std: int, scale: float, kind: str = "label") -> float:
    """printed floor of a placement kind under standard ``std`` (3: layout.policy)."""
    if int(std or 1) < 3:
        return LABEL_FLOOR * scale
    return {"label": V2.label_floor, "column": V2.column_floor, "legend": V2.legend_floor,
            "stamp": V2.stamp_floor, "marker": V2.label_floor}.get(kind, V2.label_floor)


SENT_END = re.compile(r"[.!?:;。！？：；…)\]»\"”]\s*$")
RULES = ("above", "split_paragraph", "title_block_cell", "row_number", "font_floor", "leader", "occlusion")


def _starts_lower(t: str) -> bool:
    t = (t or "").lstrip(" «\"'(-–—")
    if not t or i18n.pair().src.script == "hani":
        return False
    return t[0].islower()


def check(items: list[dict]) -> dict:
    """-> {"violations": [...], "advisory": [...], "counts": {rule: n}, "ok": bool}"""
    viol: list[dict] = []
    adv: list[dict] = []

    def hit(rule, it, why):
        (viol if (it.get("std") or 1) >= 2 else adv).append({"rule": rule, "pg": it.get("pg"), "text": (it.get("text") or "")[:60],
                                                             "why": why})
    by_pg = defaultdict(list)
    for it in items:
        by_pg[it.get("pg")].append(it)
    for pg, its in by_pg.items():
        v3 = [it for it in its if (it.get("std") or 1) >= 3]
        for it in v3:                                     # standard 3: printed size, occlusion, leaders
            ps, fs = float(it.get("ps") or 1.0), it.get("fs")
            role = it.get("role") or ("column" if it.get("kind") == "column" else "label")
            if fs is not None and fs * ps < policy_floor(3, ps, role) - 1e-3:
                hit("font_floor", it, f"{role} prints at {fs * ps:.2f} pt < {policy_floor(3, ps, role)}")
            if it.get("rule_px", 0) > 0 or it.get("ink", 0) > V2.ink_box + 1e-9:
                hit("occlusion", it, f"rule px {it.get('rule_px', 0)}, ink {it.get('ink', 0)}")
            bb, src = it.get("bb"), it.get("src")
            if it.get("kind") == "label" and role == "label" and bb and src:
                xov = min(bb[2], src[2]) - max(bb[0], src[0])
                if bb[3] <= src[1] + 0.5 and xov > 0:
                    hit("above", it, f"label bottom {bb[3]} above source top {src[1]}")
            if it.get("tb"):
                hit("title_block_cell", it, "title-block field translated in its cell")
            L = it.get("leader_len")
            if L is not None and L > (it.get("leader_max") or LEADER_MAX_SMALL) + 1e-6:
                hit("leader", it, f"leader {L:.1f} pt > {it.get('leader_max')}")
        nl = sum(1 for it in v3 if it.get("leader_len") is not None)
        if nl > LEADERS_PER_PAGE:
            hit("leader", {"pg": pg, "std": 3, "text": ""}, f"{nl} leaders on the page > {LEADERS_PER_PAGE}")
        for it in its:
            if (it.get("std") or 1) >= 3:
                continue
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
        if sum(1 for n in numbered if n) >= 2:
            for r, n in list(zip(rows, numbered))[1:]:             # the first row is the header
                if not n:
                    hit("row_number", r, "table row without its row number")
    v3all = [it for it in items if (it.get("std") or 1) >= 3 and it.get("kind") == "label" and it.get("role", "label") == "label"]
    lead = [it["leader_len"] for it in v3all if it.get("leader_len") is not None]
    if v3all and lead and (len(lead) / len(v3all) > LEADER_SHARE_MAX or sum(lead) / len(lead) > LEADER_MEAN_MAX):
        hit("leader", {"pg": None, "std": 3, "text": ""},
            f"leaders {len(lead)}/{len(v3all)} = {len(lead) / len(v3all):.1%} (max {LEADER_SHARE_MAX:.0%}), "
            f"mean {sum(lead) / len(lead):.1f} pt (max {LEADER_MEAN_MAX})")
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
