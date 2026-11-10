"""LLM input packets for the scanned route (built by code, sent by the orchestrator).

``page_notes(ctx, pg)`` -> {"data": ..., "images": [...]} for the task ``page_notes``: the
title block, the numbered-notes regions and (optionally) the lines whose numbers the two OCR
engines disagree on — each as a crop of work/png at readable resolution (≤1568 px long side),
with the OCR text of those regions.  Only these crops are sent, never the whole sheet: re-reading
page images was the largest cost of the first manual run.

Region detection is pure OCR geometry (shared with automap):
  notes     numbered clauses «1 ...», «2 ...» stacked in one column (Russian or English), with
            their continuation lines and an optional heading (Примечания, Технические требования,
            Общие указания, Notes ...) directly above
  title     title-block field names (Разраб., Проверил, Н.контр., Стадия, Листов ...) in the
            lower right; fallback: the standard 185×55 mm corner
"""
from __future__ import annotations

import re
from pathlib import Path

from scan import segments as S

MAXPX = 1568
MM = 300 / 25.4                                   # px per mm at 300 dpi
HEAD = re.compile(r"^\W*(Примечани[еяй]|Технические требования|Общие указания|"
                  r"Указания|Notes?|Technical requirements|General notes|General instructions)\W*$", re.I)
START = re.compile(r"^\W{0,2}(\d{1,2})(?:[.)]\s*|\s+)(?=[«\"(]?[А-ЯЁA-Z])")
TB = re.compile(r"Разраб|Проверил|ГИП|Н\.\s?контр|Стади|Листов|Утвердил|Утвержден|Назначение выпуска|"
                r"Нач\.\s?отд|Гл\.\s?(констр|спец)|Изм\.|Подп\.|Масштаб|Формат")


def _mm(P, box):
    return (box[0] * P.W / MM, box[1] * P.H / MM, box[2] * P.W / MM, box[3] * P.H / MM)


def title_block(P: S.Page, code_regex: str = ""):
    """Title block box (page fractions)."""
    rx = re.compile(TB.pattern + (("|" + code_regex) if code_regex else ""))
    hits = [d for d in P.lines if rx.search(d["text"]) and d["box"][1] > 0.6 and d["box"][0] > 0.3]
    wmm, hmm = P.W / MM, P.H / MM
    if len(hits) >= 2:
        far = max(hits, key=lambda d: d["box"][2] + d["box"][3])
        fx, fy = _mm(P, far["box"])[2:]
        hits = [d for d in hits if fx - _mm(P, d["box"])[0] < 200 and fy - _mm(P, d["box"])[1] < 120]
        x0 = min(d["box"][0] for d in hits); y0 = min(d["box"][1] for d in hits)
        x1 = max(d["box"][2] for d in hits); y1 = max(d["box"][3] for d in hits)
        px, py = 6 / wmm, 6 / hmm
        x0, y0 = max(0, x0 - px), max(0, y0 - py)
        x1 = min(1, max(x1 + px, 1 - 5 / wmm)); y1 = min(1, max(y1 + py, 1 - 5 / hmm))
        return (x0, y0, x1, y1)
    return (max(0, 1 - 195 / wmm), max(0, 1 - 65 / hmm), 1.0, 1.0)


def notes_regions(P: S.Page, tb=None):
    """-> [{kind: 'ru'|'en', box, head (line id or None), first (line id of clause 1), lines: [ids]}]"""
    if not P.W:
        return []
    L = sorted(P.lines, key=lambda d: (d["box"][1], d["box"][0]))
    hs = sorted(_mm(P, d["box"])[3] - _mm(P, d["box"])[1] for d in L if d["cyr"] >= 3) or [3.0]
    lh = hs[len(hs) // 2]
    ylim = tb[1] if tb else 1.0
    starters = []
    for d in L:
        m = START.match(d["text"])
        if m and len(d["text"]) >= 15 and len(re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", d["text"])) >= 3                 and re.search(r"[a-zа-яё]{3,}", d["text"]):
            lang = "ru" if S.cyr_count(d["text"]) >= 3 else "en"
            starters.append((int(m.group(1)), d, lang))
    used, regions = set(), []
    for no, d, lang in starters:
        if id(d) in used:
            continue
        x0 = _mm(P, d["box"])[0]
        seq = [d]
        for no2, d2, lang2 in starters:
            if id(d2) in used or lang2 != lang or no2 != no + len(seq):
                continue
            b2 = _mm(P, d2["box"]); bl = _mm(P, seq[-1]["box"])
            if abs(b2[0] - x0) <= 4 and 0 < b2[1] - bl[1] <= 30 * lh:
                seq.append(d2)
        head = _heading(P, L, d["box"], lh)
        if not ((len(seq) >= 2 and no == 1) or head is not None
                or (no == 1 and len(re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", d["text"])) >= 4)):
            continue
        # body: the clause lines + continuations (same column, no big vertical gap)
        members, bottom = [], _mm(P, seq[0]["box"])[1] - 0.1
        last_start = _mm(P, seq[-1]["box"])[1]
        for o in L:
            b = _mm(P, o["box"])
            if b[1] < _mm(P, seq[0]["box"])[1] - 0.5 or o["box"][1] >= ylim:
                continue
            if not (x0 - 3 <= b[0] <= x0 + 15):
                continue
            if b[1] - bottom > 2.2 * lh and b[1] > last_start:
                break
            if b[1] - bottom > 8 * lh:
                break
            members.append(o); bottom = max(bottom, b[3])
        for s2 in seq:
            if s2 not in members:
                members.append(s2)
        for o in members:
            used.add(id(o))
        allb = members + ([head] if head else [])
        box = (min(o["box"][0] for o in allb), min(o["box"][1] for o in allb),
               max(o["box"][2] for o in allb), max(o["box"][3] for o in allb))
        regions.append(dict(kind=lang, box=box, head=head["id"] if head else None, first=seq[0]["id"],
                            lines=sorted(o["id"] for o in members), n=len(seq)))
    # unnumbered prose blocks (Tesseract often drops clause numbers on text pages): ≥4 prose lines
    # in one column, continuation lines starting in lower case (a table of titles does not)
    def prose(t):
        return len(t) >= 25 and len(re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", t)) >= 3

    def lower_start(t):
        m = re.search(r"[A-Za-zА-Яа-яЁё]", t)
        return bool(m) and m.group(0).islower()
    cand = [d for d in L if id(d) not in used and d["box"][1] < ylim and prose(d["text"])]
    while cand:
        d = cand.pop(0)
        x0 = _mm(P, d["box"])[0]
        blk, bottom = [d], _mm(P, d["box"])[3]
        for o in L:
            b = _mm(P, o["box"])
            if o is d or id(o) in used or b[1] < _mm(P, d["box"])[1] or o["box"][1] >= ylim:
                continue
            if not (x0 - 4 <= b[0] <= x0 + 15):
                continue
            if b[1] - bottom > 2.5 * lh:
                break
            blk.append(o); bottom = max(bottom, b[3])
        pr = [o for o in blk if prose(o["text"])]
        cand = [c for c in cand if c not in blk]
        xs = [_mm(P, o["box"])[0] for o in blk]
        aligned = max(sum(abs(x - y) <= 6 for x in xs) for y in xs)       # left-aligned text, not centred titles
        bx = (min(o["box"][0] for o in blk), min(o["box"][1] for o in blk),
              max(o["box"][2] for o in blk), max(o["box"][3] for o in blk))
        head = _heading(P, L, bx, lh)
        # a list of titles (contents, specification names) is left-aligned too, but its lines start
        # with capitals; running text has lower-case continuation lines — or a notes heading
        bilingual = sum(bool(re.search(r"[А-Яа-яЁё].*/\s*[A-Za-z]{3,}", o["text"])) for o in blk)
        if len(pr) < 4 or aligned < 0.7 * len(blk) or bilingual >= 0.4 * len(blk) or \
                (head is None and sum(lower_start(o["text"]) for o in pr) < 0.3 * len(pr)):
            continue
        for o in blk:
            used.add(id(o))
        allb = blk + ([head] if head else [])
        box = (min(o["box"][0] for o in allb), min(o["box"][1] for o in allb),
               max(o["box"][2] for o in allb), max(o["box"][3] for o in allb))
        top = min(blk, key=lambda o: (o["box"][1], o["box"][0]))
        regions.append(dict(kind="ru" if sum(o["cyr"] >= 3 for o in blk) >= len(blk) / 3 else "en", box=box,
                            head=head["id"] if head else None, first=top["id"],
                            lines=sorted(o["id"] for o in blk), n=0))
    regions.sort(key=lambda r: (r["box"][1], r["box"][0]))
    return regions


def _heading(P, L, box, lh):
    """A notes heading directly above ``box`` (left-aligned or centred over it)."""
    bx = _mm(P, box); best = None
    for h in L:
        if not HEAD.match(h["text"].strip()):
            continue
        hb = _mm(P, h["box"])
        gap = bx[1] - hb[3]
        if -0.5 <= gap <= 6 * lh and (abs(hb[0] - bx[0]) <= 10 or abs((hb[0] + hb[2]) / 2 - (bx[0] + bx[2]) / 2) <= 15):
            if best is None or hb[1] < _mm(P, best["box"])[1]:
                best = h
    return best


# ---------------------------------------------------------------- crops
def _crop(png, P, box, pad_mm=3.0):
    """Crop a page-fraction box from the 300 dpi render; returns PIL images (split vertically at
    line gaps when needed) each ≤ MAXPX on the long side."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    im = png if not isinstance(png, (str, Path)) else Image.open(png)
    W, H = im.size
    px = pad_mm * MM
    x0, y0 = max(0, int(box[0] * W - px)), max(0, int(box[1] * H - px))
    x1, y1 = min(W, int(box[2] * W + px)), min(H, int(box[3] * H + px))
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return []
    s = min(1.0, MAXPX / w)
    s = max(s, min(0.5, MAXPX / w))        # never smaller than ~150 dpi unless the width forces it
    step = int(MAXPX / s)                  # source pixels per output tile height
    out = []
    ys = [y0]
    if h * s > MAXPX:
        # cut between text lines: candidate cut rows = gaps between line boxes
        lines = sorted((d["box"][1] * H, d["box"][3] * H) for d in P.lines
                       if ov_x(d["box"], box) and box[1] <= d["box"][1] <= box[3])
        y = y0
        while y + step < y1:
            lim = y + step
            cut = max([b1 + 2 for a1, b1 in lines if y + step * 0.5 < b1 + 2 <= lim
                       and not any(a2 < b1 + 2 < b2 for a2, b2 in lines)] or [lim])
            ys.append(int(cut)); y = int(cut)
    ys.append(y1)
    for a, b in zip(ys, ys[1:]):
        if b - a < 10:
            continue
        c = im.crop((x0, a, x1, b))
        if s < 1:
            c = c.resize((max(1, int(c.width * s)), max(1, int(c.height * s))), Image.LANCZOS)
        out.append(c.convert("L"))
    return out


def ov_x(a, b):
    return min(a[2], b[2]) - max(a[0], b[0]) > 0


def _sheet(snips, width=MAXPX):
    """Stack small crops into one image (numbered by order); ≤ MAXPX each side."""
    from PIL import Image, ImageDraw
    sheets, cur, hh = [], [], 0
    for i, im in snips:
        if im.width > width - 60:
            im = im.resize((width - 60, max(1, int(im.height * (width - 60) / im.width))))
        if cur and hh + im.height + 12 > MAXPX:
            sheets.append(cur); cur, hh = [], 0
        cur.append((i, im)); hh += im.height + 12
    if cur:
        sheets.append(cur)
    out = []
    for sh in sheets:
        S_ = Image.new("L", (min(width, max(im.width for _, im in sh) + 60), sum(im.height + 12 for _, im in sh)), 255)
        d = ImageDraw.Draw(S_); y = 0
        for i, im in sh:
            d.text((4, y + 4), f"#{i}", fill=0); S_.paste(im, (56, y + 6)); y += im.height + 12
        out.append(S_)
    return out


def doubtful_numbers(P: S.Page, tm=None, limit: int = 24):
    """Cyrillic lines whose numbers the two engines do not confirm (and, with ``tm``, whose
    Chinese would carry a number): candidates for an image check by page_notes."""
    out = []
    for d in P.lines:
        if d["cyr"] < 2 or S.count_only(d["text"]):
            continue
        txt, ok = S.reconcile(d["text"], S.rapid_over(P, d["box"]))
        if not ok or all(ok):
            continue
        if tm is not None:
            h = tm.lookup(S.make_key(txt)[0])
            if not h or not S.T.PH.search(h.zh or ""):
                continue
        out.append(d)
        if len(out) >= limit:
            break
    return out


def page_notes(ctx, pg: int, tm=None, out_dir: Path | None = None) -> dict:
    """Payload for task page_notes: {"data": {...}, "images": [Path, ...]}."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    P = S.load_page(ctx, pg)
    out_dir = Path(out_dir or ctx.w / "seg" / "crops"); out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"p{pg:02d}_*.png"):
        old.unlink()
    png = Image.open(ctx.png(pg))
    tb = title_block(P, getattr(ctx.cfg, "title_code_regex", ""))
    regs = notes_regions(P, tb)
    images: list[Path] = []

    def save(ims, tag):
        idx = []
        for k, im in enumerate(ims):
            fn = out_dir / f"p{pg:02d}_{tag}{k + 1}.png"
            im.save(fn, optimize=True); images.append(fn); idx.append(len(images) - 1)
        return idx

    def ocr_in(box):
        tl = [d["text"] for d in sorted(P.lines, key=lambda d: (d["box"][1], d["box"][0]))
              if S.ov(d["box"], box) >= 0.6]
        extra = [r["text"] for r in P.rapid if S.ov(r["box"], box) >= 0.6 and S.cyr_count(r["text"]) >= 2
                 and sum(S.ov(r["box"], d["box"]) for d in P.lines) < 0.5]
        return tl + [f"[rapid] {t}" for t in extra]

    regions = [dict(kind="title_block", images=save(_crop(png, P, tb), "tb"), ocr=ocr_in(tb))]
    for j, r in enumerate(regs, 1):
        regions.append(dict(kind="notes" if r["kind"] == "ru" else "notes_en", clauses=r["n"],
                            images=save(_crop(png, P, r["box"], pad_mm=12.0), f"n{j}_"), ocr=ocr_in(r["box"])))
    nums = []
    dn = [d for d in doubtful_numbers(P, tm) if not any(d["id"] in r["lines"] for r in regs)]
    if dn:
        snips = []
        for i, d in enumerate(dn, 1):
            c = _crop(png, P, d["box"], pad_mm=1.5)
            if c:
                snips.append((i, c[0]))
            nums.append(dict(n=i, tess=d["text"], rapid=" | ".join(S.rapid_over(P, d["box"]))[:120]))
        regions.append(dict(kind="number_check", images=save(_sheet(snips), "num"), items=nums))
    data = dict(page=pg, page_mm=[round(P.W / MM), round(P.H / MM)], regions=regions,
                expect_notes=bool(regs))
    return dict(data=data, images=images)
