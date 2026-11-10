"""Step 6 — cut the finished bilingual PDF into readable tiles for page-by-page visual review.

    python scripts/scan/40_tiles.py [--config project.toml] --round 1 [--pdf output/X.pdf] [--skip-toc N]

Each page becomes 1–24 tiles of about 1500 x 1150 px at 165 dpi (5 % overlap), enough to read
4–5 pt text.  Writes work/visual/r<round>/pNN_tK.png and tiles.json (tile -> page fraction box).
Reviewers answer per page in work/visual/r<round>/pNN.json (docs/scanned/prompt_visual_review.md).
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf  # noqa: E402
from scan import ctx as C  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--pdf", help="default: output/<code>_中文对照版.pdf")
    ap.add_argument("--skip-toc", type=int, default=-1, help="contents pages at the front (default: read from bookmarks)")
    ap.add_argument("--pages", help="only these drawing pages, e.g. 3,7-9")
    ap.add_argument("--dpi", type=float, default=165)
    a = ap.parse_args()
    cfg = C.load(a.config); ctx = C.Ctx(cfg)
    pdf = Path(a.pdf) if a.pdf else Path(cfg.output) / f"{cfg.code}_中文对照版.pdf"
    d = pymupdf.open(pdf)
    skip = a.skip_toc
    if skip < 0:
        toc = d.get_toc()
        skip = (toc[1][2] - 1) if len(toc) > 1 and toc[0][1].startswith("目录") else 0
    want = None
    if a.pages:
        want = set()
        for part in a.pages.split(","):
            x, _, y = part.partition("-"); want |= set(range(int(x), int(y or x) + 1))
    out_dir = ctx.w / "visual" / f"r{a.round}"; out_dir.mkdir(parents=True, exist_ok=True)
    tiles = {}
    for i in range(skip, d.page_count):
        pg = i - skip + 1
        if want and pg not in want:
            continue
        p = d[i]; r = p.rect
        pw, ph = r.width * a.dpi / 72, r.height * a.dpi / 72
        Cn, Rn = max(1, math.ceil(pw / 1550)), max(1, math.ceil(ph / 1150))
        tw, th = r.width / Cn, r.height / Rn; o = 0.05; k = 0
        for j in range(Rn):
            for c in range(Cn):
                k += 1
                x0, y0 = max(0, c * tw - o * tw), max(0, j * th - o * th)
                x1, y1 = min(r.width, (c + 1) * tw + o * tw), min(r.height, (j + 1) * th + o * th)
                p.get_pixmap(dpi=a.dpi, clip=pymupdf.Rect(x0, y0, x1, y1)).save(out_dir / f"p{pg:02d}_t{k}.png")
                tiles[f"p{pg:02d}_t{k}"] = [x0 / r.width, y0 / r.height, x1 / r.width, y1 / r.height, r.width, r.height]
    (out_dir / "tiles.json").write_text(json.dumps(tiles, indent=0), encoding="utf-8")
    per = {}
    for t in tiles:
        per[t.split("_")[0]] = per.get(t.split("_")[0], 0) + 1
    print(f"{len(tiles)} tiles in {out_dir}")
    print(" ".join(f"{k}:{v}" for k, v in sorted(per.items())))


if __name__ == "__main__":
    main()
