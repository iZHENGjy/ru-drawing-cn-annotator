"""Scanned-PDF route: project settings, work-folder layout and OCR file I/O.

A scanned drawing set has no text layer, so everything starts from OCR.  All per-page
files live in one work folder (``[scan] work``), named by page number::

    work/png/p07.png        300 dpi render (OCR input, crops for review)
    work/ocr/p07.tsv        Tesseract word TSV, coordinates in 300 dpi pixels
    work/lines/p07.tsv      OCR lines with ids (what the translator maps to Chinese)
    work/rapid/p07.tsv      optional second detector (RapidOCR) — catches what Tesseract misses
    work/notes/p07.json     careful translation of the page (title, numbered notes ...)
    work/map/p07.json       line id -> Chinese, extra boxes, notes anchor
    work/map/p07.fix.json   additions from the coverage audit / visual review (never hand-edit map)

Coordinates handed to the engine are PDF points, top-left origin, in the page's visual
orientation (rotation already applied), exactly like ``page.rect`` in PyMuPDF.
"""
from __future__ import annotations
import csv
import json
import os
import re
import shutil
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

K = 72.0 / 300.0                      # 300 dpi pixel -> pt
CYR = re.compile(r"[а-яА-ЯёЁ]")
csv.field_size_limit(10 ** 7)


@dataclass
class ScanConfig:
    root: Path = field(default_factory=Path.cwd)
    name: str = "scanned drawing set"
    pdf: Path | None = None
    code: str = "DOC"                     # short tag used in reports and output names
    work: Path = Path("scan")
    output: Path = Path("output")
    font: str | None = None
    tesseract: str | None = None          # tesseract.exe; default: $TESSERACT / PATH / usual install dir
    tessdata: str | None = None           # folder with rus.traineddata (tessdata_best recommended)
    lang: str = "rus+eng"
    count_word: str = "根"                 # шт./pcs. -> 根 (rebar sets) or 件 (embedded items, parts)
    title_code_regex: str = ""            # drawing-code pattern that helps find the title block
    stamp_image_size: list[int] = field(default_factory=list)   # [w, h] px of a raster approval stamp
    stamp_zh: str = ""                    # one-line Chinese for that stamp
    stamp_merge_regex: str = r"准予施工|批准用于施工"   # stamp-line translations folded into stamp_zh
    notice_zh: str = ""                   # Chinese for the vertical ownership notice in the margin
    toc_title_ru: str = ""
    toc_title_zh: str = ""
    skip_regex: str = ""                  # audit: OCR texts that never need Chinese (sheet codes ...)
    names: list[str] = field(default_factory=list)      # [keep] names: signatures, designer logo ...


def _p(root: Path, v):
    if v in (None, ""):
        return None
    p = Path(v)
    return p if p.is_absolute() else root / p


def load(path: str | Path | None = None) -> ScanConfig:
    path = Path(path or "project.toml").resolve()
    d = tomllib.loads(path.read_text(encoding="utf-8"))
    s = d.get("scan", {})
    c = ScanConfig(root=path.parent)
    for k, v in s.items():
        if hasattr(c, k):
            setattr(c, k, v)
    keep = d.get("keep", {})
    c.names = list(keep.get("names", [])) + list(keep.get("acronyms", []))
    c.font = s.get("font") or d.get("project", {}).get("font")
    c.pdf = _p(c.root, s.get("pdf") or d.get("project", {}).get("pdf"))
    c.work = _p(c.root, s.get("work", "scan"))
    c.output = _p(c.root, s.get("output", d.get("project", {}).get("output_dir", "output")))
    return c


class Ctx:
    """Paths of one scanned project.  ``Ctx(cfg).ocr(7)`` -> work/ocr/p07.tsv"""

    def __init__(self, cfg: ScanConfig):
        self.cfg = cfg
        self.w = Path(cfg.work)

    def f(self, sub: str, pg: int, ext: str) -> Path:
        return self.w / sub / f"p{pg:02d}{ext}"

    def png(self, pg): return self.f("png", pg, ".png")
    def ocr(self, pg): return self.f("ocr", pg, ".tsv")
    def lines(self, pg): return self.f("lines", pg, ".tsv")
    def rapid(self, pg): return self.f("rapid", pg, ".tsv")
    def notes(self, pg): return self.f("notes", pg, ".json")
    def map(self, pg): return self.f("map", pg, ".json")
    def fix(self, pg): return self.f("map", pg, ".fix.json")
    def audit(self, pg): return self.f("audit", pg, ".json")

    def mkdirs(self):
        for s in ("png", "ocr", "lines", "rapid", "notes", "map", "audit", "packets", "review"):
            (self.w / s).mkdir(parents=True, exist_ok=True)
        Path(self.cfg.output).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- OCR files
def read_ocr_words(tsv: Path, min_conf: float = 10):
    """Tesseract level-5 words -> [((x0,y0,x1,y1) pt, text, conf, (block,par,line))]"""
    out = []
    if not Path(tsv).exists():
        return out
    with open(tsv, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
            try:
                if r["level"] != "5" or not (r["text"] or "").strip() or float(r["conf"]) <= min_conf:
                    continue
                x, y, w, h = (int(r[k]) * K for k in ("left", "top", "width", "height"))
                out.append(((x, y, x + w, y + h), r["text"], float(r["conf"]),
                            (int(r["block_num"]), int(r["par_num"]), int(r["line_num"]))))
            except (ValueError, KeyError, TypeError):
                continue
    return out


def build_lines(tsv: Path):
    """Group OCR words into lines, numbered in reading order (60 pt bands, then x)."""
    groups = defaultdict(list)
    for bb, t, conf, key in read_ocr_words(tsv, 35):
        groups[key].append((bb, t))
    out = []
    for key in sorted(groups):
        g = groups[key]
        txt = " ".join(t for _, t in g)
        if len(re.findall(r"[A-Za-zА-Яа-яЁё]", txt)) < 2:
            continue
        out.append(dict(text=txt, x0=min(b[0] for b, _ in g), y0=min(b[1] for b, _ in g),
                        x1=max(b[2] for b, _ in g), y1=max(b[3] for b, _ in g)))
    out.sort(key=lambda d: (round(d["y0"] / 60), d["x0"]))
    for i, d in enumerate(out, 1):
        d["id"] = i
    return out


def write_lines(path: Path, lines, PW, PH):
    """lines/pNN.tsv — the file the translator maps (box as page fractions)."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("id\tx0\ty0\tx1\ty1\tcyr\ttext\n")
        for d in lines:
            fh.write(f"{d['id']}\t{d['x0']/PW:.4f}\t{d['y0']/PH:.4f}\t{d['x1']/PW:.4f}\t{d['y1']/PH:.4f}\t"
                     f"{1 if len(CYR.findall(d['text'])) >= 2 else 0}\t{d['text']}\n")


def load_lines(ctx: Ctx, pg: int, PW: float, PH: float):
    """lines/pNN.tsv -> [{id,text,x0,y0,x1,y1}] in pt (ids are the ones the map refers to)."""
    out = []
    with open(ctx.lines(pg), encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
            out.append(dict(id=int(r["id"]), text=r["text"], x0=float(r["x0"]) * PW, y0=float(r["y0"]) * PH,
                            x1=float(r["x1"]) * PW, y1=float(r["y1"]) * PH))
    return out


def load_rapid(ctx: Ctx, pg: int, PW: float, PH: float):
    """Second detector boxes: [((x0,y0,x1,y1) pt, text)].  Missing file -> []."""
    fn = ctx.rapid(pg)
    out = []
    if not fn.exists():
        return out
    for ln in open(fn, encoding="utf-8"):
        f = ln.rstrip("\r\n").split("\t")
        if len(f) < 9:
            continue
        x0, y0, x1, y1 = map(float, f[:4])
        Wd, Ht = float(f[7]), float(f[8])
        out.append(((x0 * PW / Wd, y0 * PH / Ht, x1 * PW / Wd, y1 * PH / Ht), f[4]))
    return out


def load_map(ctx: Ctx, pg: int) -> dict:
    """map/pNN.json merged with map/pNN.fix.json (extra boxes appended, line overrides, accept list)."""
    m = json.loads(ctx.map(pg).read_text(encoding="utf-8")) if ctx.map(pg).exists() else {"lines": {}}
    m.setdefault("lines", {}); m.setdefault("extra", [])
    if ctx.fix(pg).exists():
        f = json.loads(ctx.fix(pg).read_text(encoding="utf-8"))
        m["extra"] = list(m["extra"]) + list(f.get("extra") or [])
        m["lines"].update(f.get("lines") or {})
        m["accept"] = list(f.get("accept") or [])
    return m


def load_notes(ctx: Ctx, pg: int) -> dict | None:
    fn = ctx.notes(pg)
    return json.loads(fn.read_text(encoding="utf-8")) if fn.exists() else None


# ---------------------------------------------------------------- tools
def find_tesseract(cfg: ScanConfig) -> str:
    for c in (cfg.tesseract, os.environ.get("TESSERACT"), shutil.which("tesseract"),
              r"C:\Program Files\Tesseract-OCR\tesseract.exe"):
        if c and Path(c).exists():
            return str(c)
    sys.exit("tesseract not found: install it (UB Mannheim build on Windows) or set [scan] tesseract")


# same order as engine/place.py (kept here so the scan route does not need jieba)
FONT_CANDIDATES = [
    "C:/Windows/Fonts/Deng.ttf", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Light.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
]


def font_file(cfg: ScanConfig) -> str:
    for c in (cfg.font, os.environ.get("RUDRAW_FONT"), *FONT_CANDIDATES):
        if c and Path(c).is_file():
            return str(c)
    sys.exit("no Chinese font found: set RUDRAW_FONT or [scan] font")
