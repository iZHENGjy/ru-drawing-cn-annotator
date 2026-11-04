"""Step 5 (survey): translation-volume estimate per section of the album, and the share of
lines that a regex rule layer can handle (rebar marks, spacing, units, ГОСТ refs ...).
Sections come from project.toml:

    [[volume.sections]]
    name = "General notes"
    first = 1
    last = 4

Without sections the whole document is one section.
usage: 05_volume.py [--config project.toml]   ->  <review>/volume_by_section.csv"""
import argparse, csv, json, re, sys
from collections import Counter
from importlib import import_module
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import config
lines_of = import_module("02_lines").lines_of

CYR = re.compile(r"[А-Яа-яЁё]")
RULES = [r"^шаг/ ?step \d+$", r"^\d+-А\d+C?, ?L=\d+$", r"^(ГОСТ|СП|СНиП|ТУ)[\s\d.\-/A-Za-z]*$",
         r"^(шт|т|м|кг|м[²³23])\.?(/ ?\w+\.?)?( ?\d+)?$", r"^[А-Яа-я]{1,2}\d*(\.\d+)?$",
         r"^.{1,2}$", r"^Фм\d+(\.\d+)?$", r"^[\d,.\sхx=+\-/]*[А-Яа-я]{0,3}[\d,.\sхx=+\-/]*$"]
RX = [re.compile(r) for r in RULES]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    cfg = config.load(a.config)
    files = sorted(cfg.extract.glob("p*.json"))
    sections = cfg.sections or [{"name": "all", "first": 1, "last": len(files)}]
    out = []
    for s in sections:
        name, first, last = s["name"], int(s["first"]), int(s["last"])
        occ, uniq = 0, Counter()
        for p in range(first, last + 1):
            f = cfg.extract / f"p{p:03d}.json"
            if not f.exists():
                continue
            for t in lines_of(json.loads(f.read_text(encoding="utf-8"))):
                if CYR.search(t):
                    occ += 1; uniq[t.strip()] += 1
        rule = sum(1 for t in uniq if any(r.match(t) for r in RX))
        chars = sum(len(t) for t in uniq if not any(r.match(t) for r in RX))
        out.append([name, f"{first}-{last}", last - first + 1, occ, len(uniq), rule, len(uniq) - rule, chars])
    with open(cfg.review / "volume_by_section.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["section", "pages", "n_pages", "ru_line_occ", "ru_unique", "rule_layer", "need_dict", "need_dict_chars"])
        w.writerows(out)
    for r in out:
        print(r)


if __name__ == "__main__":
    main()
