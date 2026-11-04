"""PDF pipeline: put Chinese translations onto a Russian (or bilingual RU/EN) drawing PDF.

Per page:
  1. remove rotation, analyse runs, pair RU->EN, decide context (title block / body)
  2. translate RU via the translation memory; missing keys -> <review>/tm_todo.json
  3. english="keep"   : nothing of the original is removed; Chinese goes into free space
                        next to the Russian (or to a numbered callout nearby)
     english="replace": the English layer of bilingual sheets is removed glyph by glyph
                        (line art untouched) and the Chinese takes its place
  4. long numbered notes (general notes pages): in place clause-by-clause, or on an
     inserted Chinese page after the original (notes_mode="appendix")
  5. QA: Russian intact, no English left (replace mode), numbers kept, standard numbers
     equal, no Chinese over original text or over other Chinese, nothing off the page
Reports: <review>/report.csv, <review>/tm_todo.json
"""
from __future__ import annotations
import csv, json, re, time
from collections import Counter
from pathlib import Path
import pymupdf
from . import place
from .config import DEFAULT_TB_ANCHORS
from .layout import analyse, page_words, to_frame_rect, from_frame_rect, word_lang, Word
from .place import (Obstacles, Painter, pair_runs, en_slots, extra_slots_below, fit, wrap,
                    text_len, COLOR, Unit)
from .tm import TM

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
CLAUSE_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2})*)\.?\s+[А-ЯЁA-Z«\"]")
STD_RE = re.compile(r"(?:ГОСТ|СП|СНиП|ТУ|GOST|SP|SNiP|TU)\s*Р?\s*(\d[\d.\-–‑/]*\d)")


# ---------------------------------------------------------------- helpers
def parse_pages(s, n):
    out = []
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-"); out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return [p for p in out if 1 <= p <= n]


def strip_ends(run_words):
    ws = list(run_words)
    while ws and ws[0].lang == "neu":
        ws.pop(0)
    while ws and ws[-1].lang == "neu":
        ws.pop()
    return " ".join(w.text for w in ws)


def nums(s):
    """numbers in a string, decimal comma normalised, F₁200 / F1 200 / F1200 -> '1','200'"""
    s = s.replace("₁", "1 ")
    s = re.sub(r"F1(\d{3})", r"F1 \1", s)
    return [n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", s)]


def std_refs(s):
    return sorted(re.sub(r"[–‑]", "-", m) for m in STD_RE.findall(s))


def finalize_zh(zh: str) -> str:
    """House style for Chinese output."""
    if not zh:
        return zh
    zh = re.sub(r"F1 ?(\d{3})\b", r"F₁\1", zh) if re.search(r"F1 ?\d{3}", zh) else zh
    # "见技术要求第 2" -> "见技术要求第 2 条"
    zh = re.sub(r"第\s*(\d+(?:\.\d+)*)\s*([)）])?\s*$", lambda m: f"第 {m.group(1)} 条" + ("）" if m.group(2) else ""), zh)
    # full-width brackets, except inside designations such as АБВ-123(45)-01
    keep = {}
    def protect(m):
        k = f"\x00{len(keep)}\x00"; keep[k] = m.group(); return k
    zh = re.sub(r"[\w.]\(\d+\)[-\w.]", protect, zh)
    zh = zh.replace("(", "（").replace(")", "）")
    for k, v in keep.items():
        zh = zh.replace(k, v)
    if zh.count("（") > zh.count("）"):
        zh += "）"
    # doubled punctuation from Chinese value + Russian trailing separator ("编制：:")
    zh = re.sub(r"([：；，。])\s*[:;,.：；，]+", r"\1", zh)
    return zh


def marker(n):
    """①..⑳, then (21), (22) ... - never reuse a marker on one page"""
    return CIRCLED[n - 1] if n <= len(CIRCLED) else f"({n})"


def reserve(obs, rects_frame, o):
    for sl in rects_frame:
        obs.words.append(Word("■", from_frame_rect(sl, o), 1.0, "neu"))


def title_block(page, words, anchors=DEFAULT_TB_ANCHORS, left_stamp=75.0):
    """Page-coordinate rects that count as title block / margin stamp."""
    anchors = set(anchors)
    a = [w.rect for w in words if w.text in anchors]
    out = [pymupdf.Rect(0, 0, left_stamp, page.rect.height)]   # left margin stamp
    if a:
        r = pymupdf.Rect(a[0])
        for x in a[1:]:
            r |= x
        out.append(pymupdf.Rect(r.x0 - 5, r.y0 - 25, page.rect.x1, page.rect.y1))
    return out


def ctx_of(rect, tb):
    c = (rect.tl + rect.br) / 2
    return "tb" if any(c in t for t in tb) else "body"


# ---------------------------------------------------------------- free placement
def place_insert(u, obs, ign, o):
    r = u.ru.frect
    s = u.ru.size
    n_lines = len(u.ru.lines)
    for k in (0.9, 0.8, 0.7, 0.6, 0.5):
        fs = s * k
        h = fs * 1.2
        need = text_len(u.zh, fs)
        w1 = max(r.width, 4 * fs)
        nl = max(1, int(need / w1 + 0.999))
        cands = []
        if n_lines == 1 or nl == 1:
            cands.append(pymupdf.Rect(r.x1 + 0.3 * fs, r.y1 - h, r.x1 + 0.3 * fs + need + 0.5, r.y1))   # right
        for d in (0.1, 0.35, 0.7):
            cands.append(pymupdf.Rect(r.x0, r.y1 + d * s, r.x0 + w1, r.y1 + d * s + nl * h))         # below
            cands.append(pymupdf.Rect(r.x0, r.y0 - d * s - nl * h, r.x0 + w1, r.y0 - d * s))         # above
        if n_lines == 1:
            cands.append(pymupdf.Rect(r.x0 - 0.3 * fs - need - 0.5, r.y1 - h, r.x0 - 0.3 * fs, r.y1))  # left
            # tucked right under the word, may touch its box
            cands.append(pymupdf.Rect(r.x0, r.y1 - 0.05 * s, r.x0 + max(need + 0.5, r.width), r.y1 - 0.05 * s + h))
        for c in cands:
            if obs.free(c, o, ign):
                n = max(1, int(round(c.height / h)))
                slots = [pymupdf.Rect(c.x0, c.y0 + i * c.height / n, c.x1, c.y0 + (i + 1) * c.height / n) for i in range(n)]
                lines = wrap(u.zh, [sl.width for sl in slots], fs)
                if lines:
                    return fs, lines, slots
    return None, None, None


def find_free_block(obs, o, ign, r, s, bw, bh, max_ring=60):
    cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
    step = max(s, 4.0)
    for ring in range(1, max_ring):
        d = ring * step
        for k in range(-ring, ring + 1, max(1, ring // 6)):
            for (x, y) in ((cx + k * step - bw / 2, cy - d - bh), (cx + k * step - bw / 2, cy + d),
                           (cx - d - bw, cy + k * step - bh / 2), (cx + d, cy + k * step - bh / 2)):
                c = pymupdf.Rect(x, y, x + bw, y + bh)
                if obs.free(pymupdf.Rect(c.x0 - 0.3 * s, c.y0 - 0.3 * s, c.x1 + 0.3 * s, c.y1 + 0.3 * s), o, ign):
                    return c
    return None


def place_callout(u, obs, ign, o, mark):
    r = u.ru.frect
    s = u.ru.size
    mfs = s * 0.75
    mw = text_len(mark, mfs)
    mslot = None
    for c in (pymupdf.Rect(r.x1 + 0.1 * s, r.y0, r.x1 + 0.1 * s + mw, r.y0 + mfs * 1.1),
              pymupdf.Rect(r.x0 - 0.1 * s - mw, r.y0, r.x0 - 0.1 * s, r.y0 + mfs * 1.1),
              pymupdf.Rect(r.x1 - mw, r.y0 - mfs * 1.1, r.x1, r.y0)):
        if obs.free(c, o, ign, segs=False):
            mslot = c; break
    if mslot is None:
        mslot = pymupdf.Rect(r.x1 - mw, r.y0, r.x1, r.y0 + mfs * 1.1)
    text = mark + " " + u.zh
    fs = s * 0.85
    h = fs * 1.25
    width = min(text_len(text, fs) + 1, max(r.width, 30 * fs))
    nl = 1
    while True:
        lines = wrap(text, [width] * nl, fs, hard=nl > 6)
        if lines:
            break
        nl += 1
    best = find_free_block(obs, o, ign, r, s, width, nl * h)
    if best is None:
        return None
    slots = [pymupdf.Rect(best.x0, best.y0 + i * h, best.x1, best.y0 + (i + 1) * h) for i in range(nl)]
    return (mfs, mslot), (fs, lines, slots)


# ---------------------------------------------------------------- long notes (general notes pages)
def stream_lines(runs, boxes):
    """Lines of the given runs in reading order (box by box, left to right; top to bottom
    inside a box). Fragments on the same baseline inside a box are joined into one line."""
    per_box = {i: [] for i in range(len(boxes))}
    for r in runs:
        for ln in r.lines:
            fr = pymupdf.Rect(ln[0].frect)
            for w in ln[1:]:
                fr |= w.frect
            c = (fr.tl + fr.br) / 2
            for i, b in enumerate(boxes):
                if c in b:
                    per_box[i].append([fr, list(ln), r]); break
    out = []
    for i in sorted(per_box, key=lambda i: (boxes[i].x0, boxes[i].y0)):
        items = sorted(per_box[i], key=lambda t: (t[0].y0, t[0].x0))
        merged = []
        for it in items:
            if merged:
                m = merged[-1]
                vov = min(m[0].y1, it[0].y1) - max(m[0].y0, it[0].y0)
                if vov > 0.5 * min(m[0].height, it[0].height):
                    m[1] = sorted(m[1] + it[1], key=lambda w: w.frect.x0)
                    m[0] = m[0] | it[0]
                    continue
            merged.append(it)
        out.extend(tuple(m) for m in merged)
    return out


def split_clauses(lines):
    """[(num, [line,...])] ; text before the first numbered clause gets num ''"""
    out = []
    for item in lines:
        t = " ".join(w.text for w in item[1]).strip()
        m = CLAUSE_RE.match(t)
        if m and (not out or out[-1][0] == "" or _next_num(out[-1][0], m.group(1))):
            out.append((m.group(1), [item]))
        elif out:
            out[-1][1].append(item)
        else:
            out.append(("", [item]))
    return out


def _next_num(prev, cur):
    try:
        return float(cur.split(".")[0]) >= float(prev.split(".")[0])
    except ValueError:
        return True


def notes_region(runs, min_lines=4):
    """Column boxes of long numbered notes, each tagged ru/en by its long runs.
    Returns (ru_runs, en_runs, ru_boxes, en_boxes); runs of ANY language inside a box
    (also pure number / formula lines) belong to that box's stream."""
    long_runs = [r for r in runs if len(r.lines) >= min_lines and r.orient == "h" and r.lang in ("ru", "en")]
    if not long_runs:
        return [], [], [], []
    boxes, langs = [], []
    for r in long_runs:
        fr = r.frect
        for i, b in enumerate(boxes):
            if abs(b.x0 - fr.x0) < 30 and abs(b.x1 - fr.x1) < 60:
                boxes[i] = b | fr; langs[i][r.lang] += len(r.lines); break
        else:
            boxes.append(pymupdf.Rect(fr)); langs.append(Counter({r.lang: len(r.lines)}))
    boxes = [pymupdf.Rect(b.x0 - 3, b.y0 - 3, b.x1 + 3, b.y1 + 3) for b in boxes]
    tag = [l.most_common(1)[0][0] for l in langs]
    ru_boxes = [b for b, t in zip(boxes, tag) if t == "ru"]
    en_boxes = [b for b, t in zip(boxes, tag) if t == "en"]
    def inside(r, bs):
        return r.orient == "h" and any(((r.frect.tl + r.frect.br) / 2) in b for b in bs)
    return ([r for r in runs if inside(r, ru_boxes)], [r for r in runs if inside(r, en_boxes)], ru_boxes, en_boxes)


# ---------------------------------------------------------------- main
def build(pdf, pages, out_path, tm: TM, review_dir, notes_mode="inplace", notes_pages=(), dry=False,
          english="keep", tb_anchors=DEFAULT_TB_ANCHORS, left_stamp=75.0, verbose=True):
    """Build the Chinese PDF.  ``pages`` are 1-based page numbers of ``pdf``.
    Returns the per-page report rows (also written to <review_dir>/report.csv)."""
    assert english in ("keep", "replace") and notes_mode in ("inplace", "appendix")
    keep_en = english == "keep"
    if keep_en:
        notes_mode = "appendix"          # without the English slots, long notes go to a page of their own
    log = print if verbose else (lambda *a, **k: None)
    t0 = time.time()
    review_dir = Path(review_dir)
    review_dir.mkdir(parents=True, exist_ok=True)
    bad = {k: e for k, v in tm.data.items() if (e := TM.check_entry(k, v))}
    if bad:
        raise ValueError("invalid TM entries:\n" + "\n".join(f"{e} | {k}" for k, e in bad.items()))
    src = pymupdf.open(pdf)
    doc = pymupdf.open()
    rows = []
    for pn in pages:
        tm.page = pn
        doc.insert_pdf(src, from_page=pn - 1, to_page=pn - 1)
        page = doc[-1]
        page.remove_rotation()
        runs, words, other, segs = analyse(page)
        tb = title_block(page, words, tb_anchors, left_stamp)
        issues = []

        # ---- long notes stream (general notes pages)
        notes_units = []
        if pn in notes_pages:
            nru, nen, rboxes, eboxes = notes_region(runs)
            if nru:
                ids = {id(r) for r in nru + nen}
                runs = [r for r in runs if id(r) not in ids]
                notes_units = [(nru, nen, rboxes, eboxes)]
        units = pair_runs(runs)
        if keep_en:
            # the original stays untouched: Chinese of paired units goes next to the Russian,
            # English-only text is left as it is
            units = [u for u in units if u.ru is not None]
            for u in units:
                if u.en is not None:
                    u.en, u.mode = None, "insert"
        obs = Obstacles(page, words, segs)

        # ---- translate
        for u in units:
            ref = (u.ru or u.en)
            u.ctx = ctx_of(from_frame_rect(ref.frect, ref.orient), tb)
            if u.mode == "inline":
                zh, key = tm.translate(strip_ends(u.ru.words), u.ctx)
            elif u.mode == "en_only":
                zh, keys = tm.translate_lines([" ".join(w.text for w in l) for l in u.en.lines], u.ctx)
                key = " || ".join(keys)
            else:
                zh, keys = tm.translate_lines([" ".join(w.text for w in l) for l in u.ru.lines], u.ctx)
                key = " || ".join(keys)
            u.key = key
            if zh is None:
                u.status = "missing"; continue
            # number protection (full replacement only): numbers of the English being removed
            # must be in the Chinese; missing ones are taken back only if the Russian has them
            if zh and u.en is not None and u.mode != "inline":
                toks = [w.text.strip(",;:()") for w in u.en.words if w.lang == "neu" and re.search(r"\d", w.text)]
                lacking = Counter(nums(" ".join(toks))) - Counter(nums(zh))
                if lacking and u.ru is not None:
                    o = u.ru.orient; rr = u.ru.frect; s = u.ru.size
                    band = pymupdf.Rect(rr.x0 - 0.5 * s, rr.y0 - 0.3 * s, rr.x1 + 25 * s, rr.y1 + 0.3 * s)
                    near = set(nums(" ".join(w.text for w in words if w.lang != "en" and to_frame_rect(w.rect, o).intersects(band))))
                    have = set(nums(zh))
                    miss = []
                    for t in toks:
                        nt = nums(t)
                        if not (set(nt) & set(lacking)) or set(nt) <= have:
                            continue
                        if set(nt) & have:
                            # part of this code is already in the Chinese with other values:
                            # English and Russian disagree - trust the Russian, report it
                            issues.append(f"英文与俄文数字不一致[英文 {t}]: {join_ru(u.ru)[:50]}")
                            continue
                        if set(nt) <= near:
                            miss.append(re.sub(r"(\d),(\d)", r"\1.\2", t) if re.fullmatch(r"[\d,]+", t) else t)
                    if miss:
                        zh = zh.rstrip("。") + " " + " ".join(miss)
                        u.numfix = " ".join(miss)
            zh = finalize_zh(zh)
            # standard numbers must match the Russian
            if zh and u.ru is not None:
                a, b = std_refs(join_ru(u.ru)), std_refs(zh)
                if Counter(a) - Counter(b):
                    issues.append(f"标准号不一致[{' '.join(a)} → {' '.join(b)}]: {join_ru(u.ru)[:60]}")
            u.zh = zh
            if zh == "":
                u.status = "skipped"

        # ---- notes stream translation
        notes_plan = []
        for nru, nen, rboxes, eboxes in notes_units:
            rcl = split_clauses(stream_lines(nru, rboxes))
            ecl = dict((n, l) for n, l in split_clauses(stream_lines(nen, eboxes)))
            for num, items in rcl:
                text_lines = [" ".join(w.text for w in it[1]) for it in items]
                if num:
                    text_lines[0] = re.sub(r"^" + re.escape(num) + r"\.?\s+", "", text_lines[0])
                zh, _ = tm.translate_lines(text_lines, "body")
                if zh is not None:
                    zh = "\n".join(finalize_zh(z) for z in zh.split("\n"))
                    a, b = std_refs(" ".join(text_lines)), std_refs(zh)
                    if Counter(a) - Counter(b):
                        issues.append(f"标准号不一致[{' '.join(a)} → {' '.join(b)}]: 第{num}条")
                notes_plan.append((num, zh, ecl.get(num, [])))
            if any(z is None for _, z, _ in notes_plan):
                issues.append(f"附注缺译 {sum(z is None for _, z, _ in notes_plan)} 条")

        if dry:
            m = Counter(u.mode for u in units)
            long_ins = [u for u in units if u.mode == "insert" and len(u.ru.lines) >= 2]
            long_en = [u for u in units if u.mode == "en_only" and len(u.en.lines) >= 2]
            if long_ins and long_en:
                issues.append(f"疑似未配对：俄文段 {len(long_ins)} / 英文段 {len(long_en)}")
            rows.append({"page": pn, "units": len(units), "issues": issues})
            log(f"p{pn}: dry units={len(units)} missing={sum(u.status == 'missing' for u in units)} "
                  f"insert={m['insert']} en_only={m['en_only']} long_insert={len(long_ins)} long_en={len(long_en)}")
            continue

        # ---- remove English (only words whose Chinese exists)
        redacted = set()
        red_words = []
        for u in units:
            if u.en is not None and u.zh:
                for w in u.en.words:
                    if u.mode == "inline" and w.lang != "en":
                        continue
                    redacted.add(id(w)); red_words.append(w)
        for _, zh, en_items in notes_plan:
            if zh and not keep_en:
                for it in en_items:
                    for w in it[1]:
                        redacted.add(id(w)); red_words.append(w)
        if notes_mode == "appendix" and not keep_en:   # English notes go entirely when Chinese is on the appendix page
            for _, nen, _, _ in notes_units:
                for r in nen:
                    for w in r.words:
                        if id(w) not in redacted:
                            redacted.add(id(w)); red_words.append(w)
        n_annots = 0
        for w in red_words:
            for _, cr in w.chars:
                # tiny box around each glyph centre: removes this glyph only
                c = (cr.tl + cr.br) / 2
                dx, dy = max(0.15, cr.width * 0.2), max(0.15, cr.height * 0.12)
                page.add_redact_annot(pymupdf.Rect(c.x - dx, c.y - dy, c.x + dx, c.y + dy))
                n_annots += 1
                if n_annots % 150 == 0:          # annotation cost grows with count: flush in batches
                    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        ru_before = Counter(w.text for w in words if word_lang(w.text) == "ru" and id(w) not in redacted)
        bars = 0
        for a, b in segs:                     # fraction bars that belong to removed English
            r = pymupdf.Rect(a, a) | b
            if r.height > 0.8 or r.width < 3:
                continue
            above = any(w.rect.y1 <= r.y0 + 1.5 and r.y0 - w.rect.y1 < 0.6 * w.size and w.rect.x0 >= r.x0 - 2 and w.rect.x1 <= r.x1 + 2 for w in red_words)
            below = any(w.rect.y0 >= r.y1 - 1.5 and w.rect.y0 - r.y1 < 0.6 * w.size and w.rect.x0 >= r.x0 - 2 and w.rect.x1 <= r.x1 + 2 for w in red_words)
            if above and below:
                page.add_redact_annot(pymupdf.Rect(r.x0 - 0.3, r.y0 - 0.3, r.x1 + 0.3, r.y1 + 0.3)); bars += 1
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED if bars else pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        # residual English check: one extraction, glyph centres indexed on a grid
        left = {}
        for w in page_words(page):
            for c, cr in w.chars:
                m = (cr.tl + cr.br) / 2
                left.setdefault((c, int(m.x // 2), int(m.y // 2)), []).append(m)
        residual = 0
        for w in red_words:
            for c, cr in w.chars:
                if not c.isalpha():
                    continue
                m = (cr.tl + cr.br) / 2
                hit = any(abs(p.x - m.x) < 0.5 and abs(p.y - m.y) < 0.5
                          for gx in (int(m.x // 2) - 1, int(m.x // 2), int(m.x // 2) + 1)
                          for gy in (int(m.y // 2) - 1, int(m.y // 2), int(m.y // 2) + 1)
                          for p in left.get((c, gx, gy), ()))
                if hit:
                    residual += 1
                    if residual <= 4:
                        issues.append(f"英文残留: {w.text}")
                    break

        # ---- place Chinese
        painter = Painter(page)
        appendix = []
        callouts = 0
        # (a) English slots first
        for u in units:
            if not u.zh or u.en is None:
                continue
            o = u.en.orient
            slots = en_slots(u, obs, redacted)
            if not slots:
                continue
            groups = clause_groups(u.en) if "\n" in u.zh else []
            zh_cl = u.zh.split("\n")
            if len(groups) == len(zh_cl) > 1:
                if place_clauses(painter, list(zip(zh_cl, [slots[a:b] for a, b in groups])), u.en.size * 0.95, o, u):
                    continue
            base = u.en.size * 0.95
            fs, lines = fit(u.zh, slots, base)
            if fs is None:
                pitch = slots[1].y0 - slots[0].y0 if len(slots) > 1 else slots[0].height * 1.15
                more = extra_slots_below(slots[-1], pitch, o, obs, redacted)
                fs, lines = fit(u.zh, slots + more, base)
                slots = slots + more
            if fs is None:
                # does not fit where the English was: numbered callout into free space
                callouts += 1
                mark = marker(callouts)
                shim = Unit(u.en, None, "callout", zh=u.zh)
                res = place_callout(shim, obs, redacted | {id(w) for w in u.en.words}, o, mark)
                if res is not None:
                    (mfs, mslot), (fs, lines, cslots) = res
                    mslot = pymupdf.Rect(slots[0].x0, slots[0].y0, slots[0].x0 + mslot.width, slots[0].y0 + mslot.height)
                    painter.line(mark, mslot, mfs, o)
                    for t, sl in zip(lines, cslots):
                        painter.line(t, sl, fs, o)
                    u.placed.extend(cslots + [mslot])
                    reserve(obs, cslots + [mslot], o)
                    u.status = "callout"
                    continue
                fs = base * 0.5
                pitch = slots[1].y0 - slots[0].y0 if len(slots) > 1 else slots[0].height * 1.15
                while (lines := wrap(u.zh, [sl.width for sl in slots], fs, hard=True)) is None:
                    last = slots[-1]
                    slots = slots + [pymupdf.Rect(last.x0, last.y0 + pitch, last.x1, last.y1 + pitch)]
                u.status = "overflow"
            else:
                u.status = "ok"
            for t, sl in zip(lines, slots):
                painter.line(t, sl, fs, o); u.placed.append(sl)
            reserve(obs, u.placed, o)
        # (b) long notes
        for num, zh, en_items in notes_plan:
            if not zh:
                continue
            if notes_mode == "appendix" or not en_items:
                appendix.append((num, zh))
                continue
            o = "h"
            col_x1 = max(it[0].x1 for it in en_items)
            slots = [pymupdf.Rect(it[0].x0, it[0].y0, max(it[0].x1, col_x1), it[0].y1) for it in en_items]
            # extend to the column edge of each line's column
            dummy = Unit(None, None, "notes")
            if not place_clauses(painter, [((num + " " if num else "") + zh, slots)], en_items[0][1][0].size * 0.95, o, dummy, allow_shrink_to=0.6):
                appendix.append((num, zh))
                issues.append(f"第{num}条原位放不下，转附页")
        # (c) Russian without English: nearby free space, else callout
        for u in units:
            if not u.zh or u.en is not None:
                continue
            o = u.ru.orient
            ign = redacted | {id(w) for w in u.ru.words}
            fs, lines, slots = place_insert(u, obs, ign, o)
            if fs is None:
                callouts += 1
                mark = marker(callouts)
                res = place_callout(u, obs, ign, o, mark)
                if res is None:
                    u.status = "unplaced"; continue
                (mfs, mslot), (fs, lines, slots) = res
                painter.line(mark, mslot, mfs, o)
                u.status = "callout"
                slots = slots + [mslot]
            else:
                u.status = "ok"
            for t, sl in zip(lines, slots):
                painter.line(t, sl, fs, o)
            u.placed.extend(slots)
            reserve(obs, slots, o)
        if appendix and notes_mode == "appendix" and not keep_en:
            # pointer where the English notes were
            boxes = [b for nu in notes_units for b in nu[3]]
            for b in boxes:
                painter.line("本栏中文译文见本页后所附译文页，按条号对照。", pymupdf.Rect(b.x0, b.y0, b.x1, b.y0 + 16), 13, "h")
        painter.flush()

        # ---- QA
        after_words = page_words(page)
        after = Counter(w.text for w in after_words if word_lang(w.text) == "ru")
        lost = ru_before - after
        if lost:
            issues.append("俄文丢失: " + " ".join(list(lost)[:6]))
        keep = [(w.rect, w.text) for w in words if id(w) not in redacted and (keep_en or w.lang != "en")]
        overlaps = 0
        for dr, txt in painter.drawn:
            for kr, kt in keep:
                i = dr & kr
                if not i.is_empty and i.get_area() > 0.25 * min(kr.get_area(), dr.get_area()):
                    overlaps += 1
                    if overlaps <= 5:
                        issues.append(f"中文压原文: {txt[:30]} / {kt} @({kr.x0:.0f},{kr.y0:.0f})")
                    break
        # Chinese outside the page
        pr = page.rect
        for d_rect, txt in painter.drawn:
            if not (d_rect.x0 >= pr.x0 - 1 and d_rect.y0 >= pr.y0 - 1 and d_rect.x1 <= pr.x1 + 1 and d_rect.y1 <= pr.y1 + 1):
                overlaps += 1
                issues.append(f"中文出界: {txt[:30]}")
        # Chinese over Chinese
        dr = painter.drawn
        for i in range(len(dr)):
            for j in range(i + 1, len(dr)):
                a, b = dr[i][0], dr[j][0]
                x = a & b
                if not x.is_empty and x.get_area() > 0.25 * min(a.get_area(), b.get_area()):
                    overlaps += 1
                    issues.append(f"中文压中文: {dr[i][1][:20]} / {dr[j][1][:20]}")
        st = Counter(u.status for u in units)
        for u in units:
            txt = (join_ru(u.ru) if u.ru else u.en.text).replace("\n", " ")[:70]
            if u.status in ("overflow", "unplaced", "missing", "callout"):
                issues.append(f"{u.status}: {txt}")
            if getattr(u, "numfix", None):
                issues.append(f"补回数字[{u.numfix}]: {txt}")
        rows.append({"page": pn, "units": len(units), "ok": st.get("ok", 0), "missing": st.get("missing", 0),
                     "overflow": st.get("overflow", 0), "callout": st.get("callout", 0), "unplaced": st.get("unplaced", 0),
                     "ru_lost": sum(lost.values()), "en_residual": residual, "overlap": overlaps,
                     "appendix_clauses": len(appendix), "issues": issues})
        log(f"p{pn}: units={len(units)} ok={st.get('ok', 0)} missing={st.get('missing', 0)} overflow={st.get('overflow', 0)} "
              f"callout={st.get('callout', 0)} ru_lost={sum(lost.values())} en_left={residual} overlap={overlaps} "
              f"appendix={len(appendix)} ({time.time() - t0:.0f}s)")
        if appendix:
            add_appendix_pages(doc, pn, appendix, english_removed=not keep_en)
    # ---- outputs
    (review_dir / "tm_todo.json").write_text(json.dumps(
        {k: {"ru": v, "pages": sorted(tm.missing_ctx.get(k, []))} for k, v in tm.missing.items()},
        ensure_ascii=False, indent=1), encoding="utf-8")
    with open(review_dir / "report.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        cols = ["page", "units", "ok", "missing", "overflow", "callout", "unplaced", "ru_lost", "en_residual", "overlap", "appendix_clauses"]
        w.writerow(cols + ["issues"])
        for r in rows:
            w.writerow([r.get(c, "") for c in cols] + [" || ".join(r.get("issues", []))])
    if not dry:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        doc.subset_fonts()
        doc.save(out_path, garbage=3, deflate=True)
    log(f"missing TM keys: {len(tm.missing)} -> {out_path}  ({time.time() - t0:.0f}s)")
    return rows


def join_ru(run):
    return " ".join(w.text for w in run.words)


def clause_groups(run):
    starts = []
    for i, ln in enumerate(run.lines):
        t = " ".join(w.text for w in ln).strip()
        if i == 0 or re.match(r"^(\d{1,2}(\.\d{1,2})*\.?\s+\S|-\s)", t):
            starts.append(i)
    return [(a, b) for a, b in zip(starts, starts[1:] + [len(run.lines)])]


def place_clauses(painter, items, base, o, u, allow_shrink_to=0.5):
    """items: [(chinese clause, slots)]. Continuation lines align with the text after
    the clause number. One font size for all items. False if anything does not fit."""
    plan = []
    for t, sl in items:
        if not sl:
            return False
        m = re.match(r"^(\d+(?:\.\d+)*\s+|-\s+)", t)
        indent = text_len(m.group(1), base) if m else 0
        sl2 = [sl[0]] + [pymupdf.Rect(max(s.x0, sl[0].x0 + indent), s.y0, s.x1, s.y1) for s in sl[1:]]
        steps = [k for k in (1.0, 0.93, 0.86, 0.8, 0.74, 0.68, 0.62, 0.56, 0.5) if k >= allow_shrink_to]
        fs, lines = fit(t, sl2, base, steps=tuple(steps))
        if fs is None:
            return False
        plan.append((t, sl2))
        u.fs = min(getattr(u, "fs", 1e9), fs)
    fs = u.fs
    for t, sl2 in plan:
        lines = wrap(t, [s.width for s in sl2], fs) or wrap(t, [s.width for s in sl2], fs, hard=True)
        for tl, s in zip(lines, sl2):
            painter.line(tl, s, fs, o)
            u.placed.append(s)
    u.status = "ok"
    return True


def add_appendix_pages(doc, pn, clauses, english_removed=True):
    """Insert A4 portrait Chinese pages right after the current last page."""
    W, H = pymupdf.paper_size("a4")
    margin, fs, lh = 50, 10.0, 15.5
    width = W - 2 * margin
    page = None
    y = H
    n = 0
    def new_page():
        nonlocal page, y, n
        n += 1
        page = doc.new_page(width=W, height=H)
        tw = pymupdf.TextWriter(page.rect, color=(0, 0, 0))
        tw.append((margin, margin), f"第 {pn} 页 中文译文（续页 {n}）", font=place.FONT, fontsize=13)
        sub = "按原图条号对照。原图俄文保留，英文已删除。" if english_removed else "按原图条号对照。原图未作改动。"
        tw.append((margin, margin + 18), sub, font=place.FONT, fontsize=8.5)
        tw.write_text(page)
        y = margin + 40
    new_page()
    for num, zh in clauses:
        paras = zh.split("\n")
        for pi, para in enumerate(paras):
            prefix = (num + "  ") if (pi == 0 and num) else ""
            indent = text_len(num + "  ", fs) if num else 0
            first_w = width
            lines = wrap(prefix + para, [first_w] + [width - indent] * 200, fs, hard=True)
            for li, t in enumerate(lines):
                if y + lh > H - margin:
                    new_page()
                tw = pymupdf.TextWriter(page.rect, color=COLOR)
                x = margin + (indent if li > 0 or (pi > 0) else 0)
                tw.append((x, y), t, font=place.FONT, fontsize=fs)
                tw.write_text(page)
                y += lh
        y += 4
