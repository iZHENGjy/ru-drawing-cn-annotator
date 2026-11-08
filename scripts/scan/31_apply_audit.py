"""Step 5b — write the reviewer's answers to the audit packets into map/pNN.fix.json.

    python scripts/scan/31_apply_audit.py [--config project.toml]

Answers: work/answers/audit/pNN.json  (format: docs/scanned/prompt_audit_review.md)
    {"items": [{"ref": "#1", "act": "tr", "ru": "...", "zh": "..."},
               {"ref": "#2", "act": "ok", "why": "并入#1"}, {"ref": "C1", "act": "skip", "why": "人名"}]}

"tr" adds an extra box at the candidate's exact OCR box (no eyeballed coordinates);
"ok"/"skip" add an ``accept`` entry so the next audit does not report it again.
Refuses answers that skip a ref, and Chinese whose digits differ from the Russian.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scan import ctx as C  # noqa: E402


def digits(s):
    return sorted(re.findall(r"\d+(?:[.,]\d+)?", (s or "").replace(",", ".")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    a = ap.parse_args()
    ctx = C.Ctx(C.load(a.config))
    tot, bad = {}, 0
    for fn in sorted((ctx.w / "answers" / "audit").glob("p*.json")):
        pg = int(fn.stem[1:])
        snap = json.loads((ctx.w / "packets" / f"p{pg:02d}.cands.json").read_text(encoding="utf-8"))
        PW, PH = snap["PW"], snap["PH"]
        ans = json.loads(fn.read_text(encoding="utf-8"))
        F = json.loads(ctx.fix(pg).read_text(encoding="utf-8")) if ctx.fix(pg).exists() else {"extra": []}
        F["extra"] = [e for e in F.get("extra", []) if e.get("src") != "audit"]
        F["accept"] = [x for x in F.get("accept", []) if x.get("src") != "audit"]
        refs = {it.get("ref") for it in ans.get("items", [])}
        miss = [k for k in snap["cands"] if k not in refs]
        if miss:
            print(f"!! p{pg:02d}: no answer for {miss}"); bad += 1
        for it in ans.get("items", []):
            act = it.get("act"); tot[act] = tot.get(act, 0) + 1
            c = snap["cands"].get(it.get("ref"))
            if not c:
                print(f"!! p{pg:02d}: unknown ref {it.get('ref')}"); bad += 1; continue
            x0, y0, x1, y1 = c["box"]
            nb = [x0 / PW, y0 / PH, x1 / PW, y1 / PH]
            if act in ("ok", "skip"):
                F["accept"].append({"box": nb, "act": act, "why": it.get("why", ""), "ru": c.get("text", ""), "src": "audit"})
                continue
            zh = (it.get("zh") or "").strip()
            if act != "tr" or not zh:
                print(f"!! p{pg:02d}: invalid answer {it}"); bad += 1; continue
            ru = it.get("ru") or c.get("text", "")
            if digits(ru) and digits(zh) and not set(digits(zh)) <= set(digits(ru)):
                print(f"!! p{pg:02d} {it['ref']}: digits differ  ru={ru!r}  zh={zh!r}"); bad += 1; continue
            F["extra"].append({"box": nb, "zh": zh, "ru": ru, "src": "audit"})
        ctx.fix(pg).write_text(json.dumps(F, ensure_ascii=False, indent=0), encoding="utf-8")
    print(tot, "problems:", bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
