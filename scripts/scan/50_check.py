"""Step 7 — data gates before delivery (no rendering, runs in seconds).

    python scripts/scan/50_check.py [--config project.toml] [--marks "Км1.1,Км1.2,..."]

1. Member marks: every mark written in the Chinese (Км1.2, Бм2.4-1, Рм2 ...) must exist in
   the set.  The set is taken from --marks / [scan] marks, else collected from all page
   titles and notes (notes/pNN.json).  OCR often drops the dot (Км 12 for Км 1.2) and a
   translator copying OCR text would carry that into the Chinese.
2. Digits (advisory, --digits): every number in a Chinese label should also occur in the
   Russian around it (the OCR line and the lines directly below it).  On scans most hits are
   OCR misreads, so this list is for a human to skim, it does not fail the run.
Exit code 1 only for mark problems (the hard gate).
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf  # noqa: E402
from scan import ctx as C  # noqa: E402

MARK = re.compile(r"([А-ЯЁ]м|МН)\s?(\d+(?:\.\d+)?(?:-\d+)?)")


MONTHS = {"anpen": "4", "январ": "1", "феврал": "2", "март": "3", "апрел": "4", "мая": "5", "май": "5", "июн": "6", "июл": "7",
          "август": "8", "сентябр": "9", "октябр": "10", "ноябр": "11", "декабр": "12"}
OCR_DIGIT = str.maketrans({"l": "1", "I": "1", "!": "1", "|": "1", "O": "0", "О": "0", "З": "3"})


def nums(s):
    return set(re.findall(r"\d+", s or ""))


def ru_nums(s):
    """Numbers of a Russian OCR text, tolerant of OCR look-alikes (l/!/| for 1, O for 0) and month names."""
    s = s or ""
    t = re.sub(r"(?<=[\d.,])[lI!|OОЗ]|[lI!|OОЗ](?=[\d.,])|(?<=[A-Za-zА-Яа-я])[l!|](?=\W|$)", lambda m: m.group(0).translate(OCR_DIGIT), s)
    out = nums(t)
    for k, v in MONTHS.items():
        if k in s.lower():
            out.add(v)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--marks")
    ap.add_argument("--digits", action="store_true", help="also list digit differences (advisory)")
    a = ap.parse_args()
    cfg = C.load(a.config); ctx = C.Ctx(cfg)
    n = pymupdf.open(cfg.pdf).page_count
    valid = set()
    if a.marks:
        valid = {m.replace(" ", "") for m in a.marks.split(",")}
    else:
        for pg in range(1, n + 1):
            j = C.load_notes(ctx, pg)
            if j:
                for m in MARK.finditer(json.dumps(j, ensure_ascii=False)):
                    valid.add(m.group(1) + m.group(2))
    problems = []
    for pg in range(1, n + 1):
        if not ctx.lines(pg).exists():
            continue
        m = C.load_map(ctx, pg)
        r = pymupdf.open(cfg.pdf)[pg - 1].rect
        lines = C.load_lines(ctx, pg, r.width, r.height)

        def context(d):
            """the line plus the lines directly below it in the same column (multi-line labels)"""
            out = [d["text"]]
            for o in lines:
                if o is not d and o["y0"] >= d["y0"] - 1 and o["y0"] - d["y1"] < 2.6 * (d["y1"] - d["y0"]) \
                        and min(o["x1"], d["x1"]) - max(o["x0"], d["x0"]) > 0:
                    out.append(o["text"])
            return " ".join(out)
        byid = {str(d["id"]): d for d in lines}
        labels = [(f"line {k}", byid[k]["text"] if k in byid else "", context(byid[k]) if k in byid else "", v)
                  for k, v in (m.get("lines") or {}).items() if v]
        labels += [("extra", e.get("ru", ""), e.get("ru", ""), e.get("zh", "")) for e in m.get("extra", []) if e.get("zh")]
        for where, ru, ctx_ru, zh in labels:
            for mm in MARK.finditer(zh):
                mk = mm.group(1) + mm.group(2)
                base = re.sub(r"-\d+$", "", mk)
                if valid and mk not in valid and base not in valid and not re.fullmatch(r"[А-ЯЁ]м\d", mk):
                    problems.append((pg, "代号不在图名清单", where, ru, zh))
            if a.digits and ctx_ru and not re.search(r"见|第\s*\d+\s*(页|条|张)|[（(]续", zh):
                extra = nums(zh) - ru_nums(ctx_ru)
                extra = {x for x in extra if not re.search(r"(图幅|A|А)\s*" + re.escape(x), zh)}   # A1/A3 sheet formats
                if extra:
                    problems.append((pg, "数字与原文不一致", where, ctx_ru, zh + f"  [{','.join(sorted(extra))}]"))
    for p in problems:
        print(f"p{p[0]:02d}  {p[1]}  {p[2]}  ru={p[3][:60]!r}  zh={p[4][:60]!r}")
    hard = [p for p in problems if p[1] == "代号不在图名清单"]
    print(f"marks known: {len(valid)}   mark problems: {len(hard)}   digit notes: {len(problems) - len(hard)}")
    sys.exit(1 if hard else 0)


if __name__ == "__main__":
    main()
