"""Compare a run's metrics JSON with a baseline JSON (both from metrics.py).

    python scripts/qa/regress.py RUN.json --baseline BASE.json [--strict] [--tol key=value ...]

Per metric: equal / better / worse / changed (informational) / skipped (missing on either side).
Exit 1 when any metric is worse than the baseline beyond its tolerance (--strict: a changed
text/layout fingerprint also fails).  Rules: RULES below — (direction, abs tolerance, rel tolerance);
direction  'higher' = more is better, 'lower' = less is better, 'zero' = must be 0, 'same' = any
difference is worse, 'info' = never fails.  A 'higher' metric that rises beyond tolerance is
better; for coverage-like counts (zh_chars, labels) rising is only 'changed' (it can also be noise).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#            key              direction  abs_tol  rel_tol  rise_is   ('better' | 'changed')
RULES = {
    "pages":           ("same",   0,    0.0,   "changed"),
    "zh_chars":        ("higher", 0,    0.005, "changed"),
    "n_gen":           ("higher", 0,    0.0,   "changed"),
    "n_jian":          ("info",   0,    0.0,   "changed"),
    "spacing":         ("higher", 0,    0.0,   "changed"),
    "count_label":     ("higher", 0,    0.0,   "changed"),
    "leaders":         ("lower",  0,    0.0,   "better"),
    "unplaced":        ("zero",   0,    0.0,   "better"),
    "font_min":        ("higher", 0.05, 0.0,   "better"),
    "font_p10":        ("higher", 0.1,  0.0,   "better"),
    "font_median":     ("higher", 0.1,  0.0,   "better"),
    "below_floor":     ("lower",  0,    0.0,   "better"),
    "audit_remaining": ("lower",  0,    0.0,   "better"),
    "mark_gate":       ("zero",   0,    0.0,   "better"),
    "occ_flagged":     ("lower",  0,    0.02,  "better"),
    "occ_tiny":        ("lower",  0,    0.02,  "better"),
    "occ_ink":         ("lower",  0,    0.02,  "better"),
    "occ_rule":        ("lower",  0,    0.02,  "better"),
    "occ_zh_overlap":  ("lower",  0,    0.0,   "better"),
    "text_sha256":     ("info",   0,    0.0,   "changed"),
    "layout_sha256":   ("info",   0,    0.0,   "changed"),
}
FINGERPRINTS = ("text_sha256", "layout_sha256")


def judge(key, run, base, rule, tol=None):
    """-> 'equal' | 'better' | 'worse' | 'changed' | 'skipped'"""
    direction, atol, rtol, rise = rule
    if run is None or base is None:
        return "skipped"
    if key in FINGERPRINTS or not isinstance(run, (int, float)) or not isinstance(base, (int, float)):
        return "equal" if run == base else "changed"
    if tol is not None:
        atol, rtol = tol, 0.0
    allow = max(atol, rtol * abs(base))
    d = run - base
    if direction == "zero":
        return ("worse" if run != 0 else ("equal" if base == 0 else "better"))
    if abs(d) <= allow:
        return "equal"
    if direction == "info":
        return "changed"
    if direction == "same":
        return "worse"
    if direction == "higher":
        return "worse" if d < 0 else rise
    if direction == "lower":
        return "worse" if d > 0 else "better"
    return "changed"


def compare(run: dict, base: dict, strict=False, tols=None):
    """-> (rows [(key, base, run, verdict)], failed: bool)"""
    rows, failed = [], False
    for key, rule in RULES.items():
        if key not in run and key not in base:
            continue
        v = judge(key, run.get(key), base.get(key), rule, (tols or {}).get(key))
        rows.append((key, base.get(key), run.get(key), v))
        if v == "worse" or (strict and key in FINGERPRINTS and v == "changed"):
            failed = True
    return rows, failed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run"); ap.add_argument("--baseline", required=True)
    ap.add_argument("--strict", action="store_true", help="a changed text/layout fingerprint is a failure")
    ap.add_argument("--tol", action="append", default=[], metavar="KEY=ABS", help="override a metric's tolerance")
    a = ap.parse_args()
    run = json.loads(Path(a.run).read_text(encoding="utf-8"))
    base = json.loads(Path(a.baseline).read_text(encoding="utf-8"))
    tols = {k: float(v) for k, v in (t.split("=", 1) for t in a.tol)}
    rows, failed = compare(run, base, a.strict, tols)
    for key, b, r, v in rows:
        sb = (str(b)[:12] if key in FINGERPRINTS else str(b)); sr = (str(r)[:12] if key in FINGERPRINTS else str(r))
        print(f"{v:8s} {key:16s} baseline={sb:>14s} run={sr:>14s}")
    print("REGRESSION" if failed else "OK")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
