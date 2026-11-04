"""Step 2 (survey): group extracted words into lines, collect unique Cyrillic lines with
frequency -> <review>/unique_ru_lines.csv (text, count, first_page, n_pages).
This is the work list for building the translation memory.
usage: 02_lines.py [--config project.toml]"""
import argparse, csv, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

CYR = re.compile(r"[А-Яа-яЁё]")


def lines_of(words):
    """group by rounded baseline and font size; split when the horizontal gap is large"""
    buckets = defaultdict(list)
    for w in words:
        buckets[(round(w["bottom"] / 2), round(w["size"], 1))].append(w)
    out = []
    for key, ws in buckets.items():
        ws.sort(key=lambda w: w["x0"])
        cur = [ws[0]]
        for w in ws[1:]:
            if w["x0"] - cur[-1]["x1"] > w["size"] * 1.5:
                out.append(cur); cur = [w]
            else:
                cur.append(w)
        out.append(cur)
    return [" ".join(w["text"] for w in l) for l in out]


def main():
    from engine import config
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    cfg = config.load(a.config)
    cnt, first, pages = Counter(), {}, defaultdict(set)
    for f in sorted(cfg.extract.glob("p*.json")):
        p = int(f.stem[1:])
        for t in lines_of(json.loads(f.read_text(encoding="utf-8"))):
            if CYR.search(t):
                t = re.sub(r"\s+", " ", t).strip()
                cnt[t] += 1; first.setdefault(t, p); pages[t].add(p)
    cfg.review.mkdir(parents=True, exist_ok=True)
    with open(cfg.review / "unique_ru_lines.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh); w.writerow(["text", "count", "first_page", "n_pages"])
        for t, c in cnt.most_common():
            w.writerow([t, c, first[t], len(pages[t])])
    print("cyr line occurrences", sum(cnt.values()), "unique", len(cnt), "unique chars", sum(len(t) for t in cnt))


if __name__ == "__main__":
    main()
