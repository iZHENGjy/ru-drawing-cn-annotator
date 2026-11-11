"""Paragraphs of a scanned page (docs/output_standard.md, ``[scan] output_standard = 2``).

OCR reads running text line by line; a sentence must be translated whole.  ``paragraphs`` joins
consecutive OCR lines of one paragraph (same column, small gap, no paragraph break) so that
segments / automap / the engine can treat the paragraph as one text:
- ``prose``: a paragraph of running text (>= 2 lines, >= 8 Russian words) -> extension column;
- ``tb``:    a multi-line text inside the title block -> one entry of the ``图签译文`` line.
Boxes are in the units of the input (normalised in segments/automap).
"""
from __future__ import annotations
import re

CYRW = re.compile(r"[А-Яа-яЁё]{2,}")
BREAK_START = re.compile(r"^(?:[-–—•]\s|\d{1,2}(?:\.\d{1,2})*[.)]?\s+[А-ЯЁA-Z«\"]|[а-яё]\)\s)")
SENT_END = re.compile(r"[.:;!?»)]\s*$")


def words(t: str) -> int:
    return len(CYRW.findall(t or ""))


def join(texts) -> str:
    out = ""
    for t in texts:
        t = (t or "").strip()
        if not out:
            out = t
        elif re.search(r"[а-яёa-z]-$", out) and re.match(r"^[а-яёa-z]", t):
            out = out[:-1] + t
        else:
            out += " " + t
    return re.sub(r"\s+", " ", out).strip()


def _breaks(par, ln):
    prev, t, pt = par[-1], ln["text"].strip(), par[-1]["text"].strip()
    if BREAK_START.match(t):
        return True
    if re.search(r"[-,]$", pt) or re.match(r"^[а-яё(«\"]", t):
        return False
    x0s = [l["box"][0] for l in par] + [ln["box"][0]]
    right = max([l["box"][2] for l in par] + [ln["box"][2]])
    width = right - min(x0s)
    short = prev["box"][2] < right - 0.15 * width
    indent = ln["box"][0] - min(l["box"][0] for l in par) > 1.2 * (ln["box"][3] - ln["box"][1])
    if SENT_END.search(pt):
        return short or indent
    return prev["box"][2] < right - 0.25 * width and t[:1].isupper()


def paragraphs(lines, skip=()):
    """lines: [{id, box(x0,y0,x1,y1), text}] -> [{ids, box, ru}] (multi-line groups only)."""
    ls = sorted((l for l in lines if l["id"] not in skip and words(l["text"]) >= 1),
                key=lambda l: (l["box"][1], l["box"][0]))
    pars = []
    for ln in ls:
        b = ln["box"]; h = b[3] - b[1]
        best = None
        for par in pars:
            lb = par[-1]["box"]
            gap = b[1] - lb[3]
            hh = max(h, lb[3] - lb[1])
            if not (-0.4 * hh <= gap < 0.9 * hh):
                continue
            px0 = min(l["box"][0] for l in par); px1 = max(l["box"][2] for l in par)
            ov = min(b[2], px1) - max(b[0], px0)
            if ov < 0.5 * min(b[2] - b[0], px1 - px0):
                continue
            lx0 = par[-1]["box"][0]
            if abs(b[0] - lx0) > 2.5 * hh and abs((b[0] + b[2]) - (lb[0] + lb[2])) / 2 > 2 * hh:
                continue                                      # neither left-aligned nor centred: another cell
            if best is None or lb[3] > best[-1]["box"][3]:
                best = par
        if best is not None and not _breaks(best, ln):
            best.append(ln)
        else:
            pars.append([ln])
    out = []
    for par in pars:
        if len(par) < 2:
            continue
        box = (min(l["box"][0] for l in par), min(l["box"][1] for l in par),
               max(l["box"][2] for l in par), max(l["box"][3] for l in par))
        out.append(dict(ids=[l["id"] for l in par], box=box, ru=join(l["text"] for l in par)))
    return out


def classify(pars, in_tb):
    """-> pars with kind 'prose' / 'tb'; others dropped."""
    out = []
    for p in pars:
        if in_tb(p["box"]):
            if not re.search(r"[|\[\]]", p["ru"]):            # a line that runs across title-block cells is no paragraph
                out.append(dict(p, kind="tb"))
        elif words(p["ru"]) >= 8:
            out.append(dict(p, kind="prose"))
    return out
