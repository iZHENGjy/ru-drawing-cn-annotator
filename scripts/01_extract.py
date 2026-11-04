"""Step 1 (survey): extract the words of every page into <extract>/p###.json and write a
census (page, size, rotation, word count, Cyrillic / Latin word count) to <review>/census.csv.
usage: 01_extract.py [--config project.toml] [--pdf in.pdf]"""
import argparse, csv, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pdfplumber
from engine import config

CYR = re.compile(r"[А-Яа-яЁё]")
LAT = re.compile(r"[A-Za-z]")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--pdf", default=None)
    a = ap.parse_args()
    cfg = config.load(a.config)
    pdf_path = a.pdf or cfg.pdf
    cfg.extract.mkdir(parents=True, exist_ok=True)
    cfg.review.mkdir(parents=True, exist_ok=True)
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False,
                                       extra_attrs=["fontname", "size"])
            data = [{k: w[k] for k in ("text", "x0", "top", "x1", "bottom", "fontname", "size")} for w in words]
            (cfg.extract / f"p{i:03d}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            cyr = sum(1 for w in words if CYR.search(w["text"]))
            lat = sum(1 for w in words if LAT.search(w["text"]) and not CYR.search(w["text"]))
            rows.append([i, round(float(page.width)), round(float(page.height)), page.rotation, len(words), cyr, lat])
    with open(cfg.review / "census.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["page", "w", "h", "rotate", "words", "cyr_words", "lat_words"]); w.writerows(rows)
    print("pages", len(rows), "words", sum(r[4] for r in rows), "cyr", sum(r[5] for r in rows), "lat", sum(r[6] for r in rows))
    if rows and sum(r[4] for r in rows) == 0:
        print("no text layer: this PDF is a scan / SHX vector export and needs OCR first (see README, Limitations)")


if __name__ == "__main__":
    main()
