"""Step 6b — write visual-review findings back, snapped to real OCR boxes.

    python scripts/scan/41_apply_visual.py [--config project.toml] --round 1

Reads work/visual/r<round>/pNN.json.  Each "漏译" (missing) / "英文无中文" finding with a
Chinese suggestion becomes an extra box in map/pNN.fix.json.  The reviewer's rough box is
only used to *find* the text: it is snapped to the nearest RapidOCR / Tesseract box whose
text matches (difflib >= 0.55), so no eyeballed coordinates end up in the data.
шт./pcs and ш./sp labels and standard title-block fields are normally handled by the engine
and are only listed, except in round >= 2 where remaining misses are added as manual labels.
"遮挡" (illegible / overlapping) and "错译" (wrong) findings go to visual/r<round>/_todo.tsv
for a human or a follow-up fix; they are not applied automatically.
"""
import argparse
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf  # noqa: E402
from scan import ctx as C  # noqa: E402

TB_FIELDS = ("替代存档号|签字及日期|原件存档号|签字|日期|修改|处数|页|文件号|编制|校核|批准|标准化审查|设计总负责人|"
             "主任专业工程师|部门主任|阶段|总页数|页次|版次|比例|发布目的|状态|会签|版权声明")
TB_RE = re.compile(rf"({TB_FIELDS}|图幅.*)([ /;、]+({TB_FIELDS}))*")
COUNT_RE = re.compile(r"/?\s*(\d[\d+()]*\s*)?(根|件|个|间距\s*\d*)")


def norm(s):
    return re.sub(r"[^0-9a-zа-яё]", "", (s or "").lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--round", type=int, default=1)
    a = ap.parse_args()
    cfg = C.load(a.config); ctx = C.Ctx(cfg)
    vdir = ctx.w / "visual" / f"r{a.round}"
    tiles = json.loads((vdir / "tiles.json").read_text(encoding="utf-8"))
    src_tag = f"visual{a.round}"
    doc = pymupdf.open(cfg.pdf)
    todo, added, nosnap = [], 0, 0
    for fn in sorted(vdir.glob("p*.json")):
        pg = int(fn.stem[1:3])
        v = json.loads(fn.read_text(encoding="utf-8"))
        nt = sum(1 for k in tiles if k.startswith(f"p{pg:02d}_"))
        if len(set(v.get("checked") or [])) < nt:
            print(f"!! p{pg:02d}: only {len(set(v.get('checked') or []))}/{nt} tiles reviewed")
        PW, PH = doc[pg - 1].rect.width, doc[pg - 1].rect.height
        boxes = list(C.load_rapid(ctx, pg, PW, PH)) + [((d["x0"], d["y0"], d["x1"], d["y1"]), d["text"])
                                                        for d in C.load_lines(ctx, pg, PW, PH)]
        F = json.loads(ctx.fix(pg).read_text(encoding="utf-8")) if ctx.fix(pg).exists() else {"extra": []}
        F["extra"] = [e for e in F.get("extra", []) if e.get("src") not in (src_tag, "manual-" + src_tag)]
        for it in v.get("issues") or []:
            tk = f"p{pg:02d}_{it.get('tile')}"
            if tk not in tiles:
                print("!! unknown tile", tk); continue
            tx0, ty0, tx1, ty1, OW, OH = tiles[tk]
            w = it.get("where") or [0, 0, 1, 1]
            bx = [(tx0 + (tx1 - tx0) * w[0]) * OW, (ty0 + (ty1 - ty0) * w[1]) * OH,
                  (tx0 + (tx1 - tx0) * w[2]) * OW, (ty0 + (ty1 - ty0) * w[3]) * OH]
            zq = re.sub(r"[(（].*", "", it.get("zh") or "").strip()
            manual = False
            if COUNT_RE.fullmatch(zq) or re.search(r"шт\.|ш\./|pcs|sp\.", it.get("text") or ""):
                if a.round < 2 or it.get("type") != "漏译":
                    todo.append((pg, "数量标注(引擎处理)", it.get("text"), it.get("zh"), [])); continue
                zq = cfg.count_word if re.search(r"шт|pcs", it.get("text") or "", re.I) else "间距"
                manual = True
            if TB_RE.fullmatch(zq):
                todo.append((pg, "图签栏目(对照行)", it.get("text"), it.get("zh"), [])); continue
            if it.get("type") in ("遮挡", "错译") or not zq:
                todo.append((pg, it.get("type"), it.get("text"), it.get("zh"), [round(b) for b in bx])); continue
            if bx[0] > PW:
                todo.append((pg, "加宽栏内", it.get("text"), it.get("zh"), [round(b) for b in bx])); continue
            cx0, cy0, cx1, cy1 = bx; mw, mh = (cx1 - cx0) * 0.8 + 6, (cy1 - cy0) * 1.2 + 6
            tn = norm(it.get("text")); best = None
            for bb, t in boxes:
                if bb[2] < cx0 - mw or bb[0] > cx1 + mw or bb[3] < cy0 - mh or bb[1] > cy1 + mh:
                    continue
                tt = norm(t)
                if not tt or not tn:
                    continue
                sm = difflib.SequenceMatcher(None, tn, tt)
                score = sm.ratio() if len(tt) <= len(tn) * 1.5 else \
                    sm.find_longest_match(0, len(tn), 0, len(tt)).size / max(len(tn), 1) * 0.9
                if best is None or score > best[0]:
                    best = (score, bb)
            if best and best[0] >= 0.55:
                bb = best[1]
            else:
                bb = bx; nosnap += 1
            F["extra"].append({"box": [bb[0] / PW, bb[1] / PH, bb[2] / PW, bb[3] / PH], "zh": zq,
                               "ru": it.get("text", ""), "src": ("manual-" if manual else "") + src_tag,
                               "snap": round(best[0], 2) if best else 0})
            added += 1
        ctx.fix(pg).write_text(json.dumps(F, ensure_ascii=False, indent=0), encoding="utf-8")
    with open(vdir / "_todo.tsv", "w", encoding="utf-8") as fh:
        for t in todo:
            fh.write("\t".join(map(str, t)) + "\n")
    print(f"added {added} (not snapped to an OCR box: {nosnap}); left for manual handling: {len(todo)} -> {vdir / '_todo.tsv'}")


if __name__ == "__main__":
    main()
