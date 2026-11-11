"""Volume-level dedupe of OCR text (scanned route).

Every Russian text of a volume is read twice — Tesseract lines (``lines/pNN.tsv``) and
RapidOCR boxes (``rapid/pNN.tsv``) — and most of it repeats from sheet to sheet (title block,
table headers, «Изделие закладное», «Общие указания смотрите ...»).  Translating it once per
*distinct text* instead of once per page line is the main cost lever of the scan route.

    normalise  ё→е, @/⌀/ø before a digit → Ø, ФОСТ/TOCT/СОЗТ → ГОСТ/GOST, Latin look-alikes
               inside Cyrillic words → Cyrillic (Tpy6a → Труба), dashes, quotes, spacing
    key        numbers → ⟨1⟩ ⟨2⟩ ... (rules of engine/tm.py), the unit the TM stores
    fold       key compared for equality: lower case, Latin look-alikes folded, no spacing noise
    fuzzy      OCR variants of the same text are merged when their letters are nearly equal
               (difflib) AND their number signature (Ø⟨n⟩, ⟨n⟩x⟨n⟩ ...) is identical

``collect(ctx)`` writes ``work/seg/segments.json``.  ``reconcile`` is the digit guard: a number
counts only when the two OCR engines (or a label read from the image) agree on it.
"""
from __future__ import annotations

import csv
import difflib
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from engine import tm as T

CYR = re.compile(r"[а-яА-ЯёЁ]")
LETTER = re.compile(r"[A-Za-zА-Яа-яЁё]")
# Latin glyphs that Tesseract/RapidOCR return for (italic) Cyrillic letters
LAT2CYR = str.maketrans("AaBCcEeHKkMmOoPpTXxYyunr", "АаВСсЕеНКкМтОоРрТХхУуипг")
FOLD = str.maketrans("aehopcxkmtbyunr", "аенорсхкмтвуипг")       # used only for comparing
DIG2CYR = {"6": "б", "3": "з", "0": "о"}
LOOKALIKE = set("AaBCcEeHKkMmOoPpTXxYyunr")
COUNT_TOK = re.compile(r"(шт|ш|wt|w|m|lut|lu|wit|wi|wr)\.?\s*/\s*(p[cсe]+[sѕ5з]?|[рp]с[sз5]?|pc5|sp|ср|5р|\$р)\.?\s*[\d+()]*"
                       r"|шт\.\s*[кk]?\s*[\d+]+|\b(p[cс]+s|sp)\.\s*[\d+()]+", re.I)


# ---------------------------------------------------------------- normalisation
def _fix_word(w: str) -> str:
    """Latin look-alikes inside a Cyrillic word -> Cyrillic; Tpy6a (all look-alikes with a
    digit inside letters) -> Труба."""
    if not LETTER.search(w):
        return w
    has_cyr = bool(CYR.search(w))
    has_lat = bool(re.search(r"[A-Za-z]", w))
    if has_cyr and has_lat:
        w = w.translate(LAT2CYR)
    elif not has_cyr and len(w) >= 4 and all(c in LOOKALIKE or c in DIG2CYR for c in w) \
            and re.search(r"[A-Za-z][630][A-Za-z]", w):
        w = w.translate(LAT2CYR)
    if CYR.search(w):    # a single 6/3/0 between lower-case Cyrillic letters is a letter
        w = re.sub(r"(?<=[а-я])[630](?=[а-я])", lambda m: DIG2CYR[m.group(0)], w)
    return w


_MARKLAT = re.compile(r"(?<![A-Za-zА-Яа-яЁё])([A-ZА-ЯЁ])([mм]|[HН])(?=\s?\d)")


def norm_marks(text: str) -> str:
    """Member marks written with Latin look-alikes -> Cyrillic (MH4 -> МН4, Bm2.5 -> Вм2.5), so
    mark sets from notes, OCR and translations compare equal."""
    def conv(m):
        a, b = m.group(1), m.group(2)
        if b in "HН" and a not in "MМ":
            return m.group(0)
        a2 = a.translate(LAT2CYR) if a.isascii() else a
        if a.isascii() and a2 == a:            # not a look-alike letter (L, S, ...)
            return m.group(0)
        return a2 + ("Н" if b in "HН" else "м")
    return _MARKLAT.sub(conv, text or "")


def lookalike_cyr(text: str, per_word: bool = False) -> str | None:
    """A Latin-only reading whose letters are all Cyrillic look-alikes (Macca -> Масса), else None.
    ``per_word``: convert only such words of ≥3 letters (Macca/Weight -> Масса/Weight)."""
    t = text or ""
    if CYR.search(t):
        return None
    if per_word:
        hits = 0

        def conv(m):
            nonlocal hits
            w = m.group(0)
            if len(w) >= 3 and all(c in LOOKALIKE for c in w):
                hits += 1
                return w.translate(LAT2CYR)
            return w
        out = re.sub(r"[A-Za-z]+", conv, t)
        return out if hits else None
    letters = re.findall(r"[A-Za-z]", t)
    if len(letters) < 3 or any(c not in LOOKALIKE for c in letters):
        return None
    return t.translate(LAT2CYR)


def norm(text: str) -> str:
    """Canonical text of one OCR reading (numbers kept)."""
    s = unicodedata.normalize("NFC", text or "")
    s = s.replace("ё", "е").replace("Ё", "Е")
    s = re.sub(r"[⌀ø∅]", "Ø", s)
    s = re.sub(r"@\s?(?=\d)", "Ø", s)                      # @220 -> Ø220 (OCR of the diameter sign)
    s = re.sub(r"[–—−‒]", "-", s)
    s = re.sub(r"[«»“”„‟\"]", '"', s).replace("‚", ",").replace("’", "'")
    s = re.sub(r"\b[ГTF][OО][CС][TТ]\b", "ГОСТ", s)          # FOCT / TOCT / ГOCT
    s = re.sub(r"\b[СC][OО][З3][TТ]\b", "GOST", s)           # GOST read as Cyrillic СОЗТ
    s = re.sub(r"[A-Za-zА-Яа-яЁё0-9]+", lambda m: _fix_word(m.group(0)), s)
    s = re.sub(r"^[\s|{}\[\]_~`'\\]+|[\s|{}\[\]_~`'\\]+$", "", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*/\s*", " / ", s)
    s = re.sub(r"\s+([,.;:)])", r"\1", s)
    s = re.sub(r"([(])\s+", r"\1", s)
    return s.strip()


def make_key(text: str):
    """raw OCR text -> (key with ⟨n⟩, numbers, prefix, suffix) via engine/tm.py rules."""
    s = T.join_lines(norm(text))
    pre, core, suf = T.split_affixes(s)
    key, nums = T.make_key(core)
    return key, nums, pre, suf


def fold(key: str) -> str:
    """Equality form of a key (case, look-alikes, spacing and trailing dots do not count)."""
    f = key.lower().replace("ё", "е").translate(FOLD)
    f = re.sub(r"\s+", " ", f)
    f = re.sub(r"\s*([./,;:()\-=×*])\s*", r"\1", f)
    f = re.sub(r"\s*/\s*", "/", f)
    return f.strip(" .,;:")


def skeleton(key: str) -> str:
    """Letters only (for fuzzy comparison)."""
    return "".join(re.findall(r"[a-zа-я]", fold(key)))


def sig(key: str) -> str:
    """Number signature: placeholders with the symbols that change their meaning, plus the code
    letters glued to a number (Бм⟨1⟩, МН⟨1⟩, А⟨1⟩С): keys that differ in a member-mark or grade
    letter are different things and must never be fuzzy-matched."""
    codes = [fold(m) for m in re.findall(r"(?<![A-Za-zА-Яа-яЁё])[A-Za-zА-Яа-яЁё]{1,3}(?=⟨)|(?<=⟩)[A-Za-zА-Яа-яЁё]{1,2}(?![A-Za-zА-Яа-яЁё])", key)]
    return "".join(re.findall(r"Ø|⟨\d+⟩|[=×+№*]", key)) + ("|" + ",".join(codes) if codes else "")


def key_id(fkey: str) -> str:
    return "k" + hashlib.sha1(fkey.encode("utf-8")).hexdigest()[:10]


def similar(a: str, b: str) -> bool:
    """Fuzzy equality of two skeletons: ≥5 letters, at most ~12 % edits (min 1)."""
    la, lb = len(a), len(b)
    if min(la, lb) < 5:
        return False
    allowed = max(1, round(0.12 * max(la, lb)))
    if abs(la - lb) > allowed:
        return False
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < 0.7 or sm.quick_ratio() < 0.7:
        return False
    r = sm.ratio()
    if (1 - r) * (la + lb) / 2 > allowed + 1e-9:
        return False
    for tag, i1, i2, j1, j2 in sm.get_opcodes():     # a missing / extra word at either end is
        if tag != "equal" and max(i2 - i1, j2 - j1) >= 3 and (i1 == 0 or i2 == la):   # other text
            return False
    return True


def cyr_count(t: str) -> int:
    return len(CYR.findall(t or ""))


def count_only(t: str) -> bool:
    """шт./pcs.N, ш./sp.N and nothing else that needs Chinese (the engine labels them)."""
    if not COUNT_TOK.search(t or ""):
        return False
    rest = COUNT_TOK.sub(" ", t)
    return not re.search(r"[А-Яа-яЁё]{3,}", rest)


# ---------------------------------------------------------------- digit guard
NUMTOK = re.compile(r"(?:(Ø)|(?<![A-Za-zА-Яа-яЁё])([GФф]))?\s?(\d+(?:[.,]\d+)*)")
#  group 1: an explicit diameter sign; group 2: a glyph OCR reads for Ø (G34, Ф34) — a sign only
#  when the other engine confirms it; group 3: the number (same numbers, same order as tm.NUM)


def _cands(m) -> set[str]:
    """Readings of one number token: plain value, and Ø+stem when it may carry a diameter sign
    (explicit Ø/@, a G/Ф glyph, or a leading 0/2 that is the sign misread)."""
    n = m.group(3).replace(",", ".")
    out = {n}
    if m.group(1) or m.group(2):
        out.add("Ø" + n)
    if re.fullmatch(r"[02]\d{2,4}", n):
        out.add("Ø" + n[1:])
    return out


def num_tokens(text: str) -> list[tuple[str, str]]:
    """[(prefix 'Ø' or '', number)] of a text after normalisation."""
    return [(m.group(1) or "", m.group(3).replace(",", ".")) for m in NUMTOK.finditer(norm(text))]


def reconcile(text: str, others: list[str]):
    """Digit guard for one OCR reading.  ``others`` are the texts of the same box read by the
    other engine (and labels page_notes read from the image).  Returns (text, ok): ``text`` is
    normalised and ``ok[i]`` tells whether the i-th number (make_key order) is confirmed.

    Ø / @ / G / 0 / 2 confusion is handled explicitly: «2219» vs «Ø219», «G34» vs «034»,
    «234» vs «034» agree on Ø219 / Ø34 and the text is rewritten with the sign; identical
    readings («2219» twice) are kept as read."""
    s = norm(text)
    plain, diam = set(), set()
    for o in others:
        o = re.sub(r"(?<![A-Za-zА-Яа-яЁё])[OoОо](?=\d)|(?<=\d)[OoОо](?![A-Za-zА-Яа-яЁё])", "0", norm(o))  # О09
        for m in NUMTOK.finditer(o):
            c = _cands(m)
            plain.add(m.group(3).replace(",", "."))
            diam |= {x for x in c if x.startswith("Ø")}
    ok, out, last = [], [], 0
    for m in NUMTOK.finditer(s):
        nv = m.group(3).replace(",", ".")
        mine = {x for x in _cands(m) if x.startswith("Ø")}
        if m.group(1) and nv in plain:
            good, rep = True, None                             # Ø219 here, 219 there
        elif nv in plain and not (m.group(2) and mine & diam):
            good, rep = True, None
        elif mine & diam:
            good, rep = True, sorted(mine & diam)[0]           # both readings say Ø + the same stem
        else:
            good, rep = False, None
        ok.append(good)
        if rep and rep != "Ø" + m.group(3):
            out.append(s[last:m.start()] + (" " if m.start() > 0 and s[m.start()] == " " else "") + rep)
            last = m.end()
        elif rep and m.group(2):
            out.append(s[last:m.start(2)] + "Ø"); last = m.start(3)
    out.append(s[last:])
    return "".join(out), ok


# ---------------------------------------------------------------- page data (page fractions)
def ov(a, b) -> float:
    """share of box a covered by box b"""
    ix = min(a[2], b[2]) - max(a[0], b[0]); iy = min(a[3], b[3]) - max(a[1], b[1])
    if ix <= 0 or iy <= 0:
        return 0.0
    return ix * iy / max((a[2] - a[0]) * (a[3] - a[1]), 1e-12)


@dataclass
class Page:
    pg: int
    lines: list = field(default_factory=list)   # {id, box, text, cyr}
    rapid: list = field(default_factory=list)   # {i, box, text, score}
    words: list = field(default_factory=list)   # (box, text, conf)  Tesseract words
    W: int = 0
    H: int = 0                                  # 300 dpi pixels


_BIL = re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9])([A-Za-zА-Яа-яЁё0-9]{2,8})(\.?\s?/\s?)(?=[A-Za-z]{3,})")


def bilingual_ru(text: str) -> str:
    """RU/EN pairs whose Russian half OCR read with Latin look-alikes: «Tp./pipe» -> «Тр./pipe»,
    «Tpy6a/Pipe» -> «Труба/Pipe», «Tpyбa/Pipe» -> «Труба/Pipe».  Only the word right before
    «/English» is touched, and only when every character is Cyrillic or a look-alike."""
    def conv(m):
        w = m.group(1)
        if not re.search(r"[A-Za-z]", w) or not re.match(r"[A-Za-zА-Яа-яЁё]", w):
            return m.group(0)
        if not all(c in LOOKALIKE or CYR.match(c) or c in DIG2CYR for c in w):
            return m.group(0)
        w2 = re.sub(r"(?<=[A-Za-zА-Яа-яЁё])[630](?=[A-Za-zА-Яа-яЁё])", lambda d: DIG2CYR[d.group(0)], w)
        if re.search(r"\d", w2):
            return m.group(0)
        return w2.translate(LAT2CYR) + m.group(2)
    return _BIL.sub(conv, text or "")


def load_page(ctx, pg: int, words: bool = True) -> Page:
    P = Page(pg)
    fn = ctx.lines(pg)
    if fn.exists():
        with open(fn, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
                t = bilingual_ru(r["text"] or "")
                P.lines.append(dict(id=int(r["id"]), box=tuple(float(r[k]) for k in ("x0", "y0", "x1", "y1")),
                                    text=t, cyr=cyr_count(t)))
    fn = ctx.rapid(pg)
    if fn.exists():
        for i, ln in enumerate(open(fn, encoding="utf-8")):
            f = ln.rstrip("\r\n").split("\t")
            if len(f) < 9:
                continue
            Wd, Ht = float(f[7]), float(f[8])
            P.W, P.H = int(Wd), int(Ht)
            x0, y0, x1, y1 = map(float, f[:4])
            P.rapid.append(dict(i=i, box=(x0 / Wd, y0 / Ht, x1 / Wd, y1 / Ht), text=bilingual_ru(f[4]),
                                score=float(f[5] or 0)))
    fn = ctx.ocr(pg)
    if fn.exists() and (words or not P.W):
        with open(fn, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
                try:
                    if r["level"] == "1":
                        P.W, P.H = int(r["width"]), int(r["height"])
                        continue
                    if not words or r["level"] != "5" or not (r["text"] or "").strip() or float(r["conf"]) <= 10:
                        continue
                    x, y, w, h = (int(r[k]) for k in ("left", "top", "width", "height"))
                    P.words.append(((x, y, x + w, y + h), r["text"], float(r["conf"])))
                except (ValueError, KeyError, TypeError):
                    continue
        if P.W:
            P.words = [((b[0] / P.W, b[1] / P.H, b[2] / P.W, b[3] / P.H), t, c) for b, t, c in P.words]
    return P


def rapid_over(P: Page, box, thr=0.3):
    """RapidOCR texts read on (roughly) the same place as ``box``."""
    return [r["text"] for r in P.rapid if ov(box, r["box"]) >= thr or ov(r["box"], box) >= thr]


def words_over(P: Page, box, thr=0.5):
    return [t for b, t, _ in P.words if ov(b, box) >= thr]


def neighbours(items, it):
    """Closest text directly above / below (overlapping in x)."""
    bx = it["box"]; up = dn = None
    for o in items:
        if o is it:
            continue
        b = o["box"]
        if min(b[2], bx[2]) - max(b[0], bx[0]) <= 0:
            continue
        if b[3] <= bx[1] + 0.002 and (up is None or b[3] > up[0]):
            up = (b[3], o["text"])
        elif b[1] >= bx[3] - 0.002 and (dn is None or b[1] < dn[0]):
            dn = (b[1], o["text"])
    return (up[1][:80] if up else ""), (dn[1][:80] if dn else "")


def page_texts(P: Page):
    """The Russian readings of a page that need a decision:
    [(src, id, box, text, needed)].  A Tesseract line with ≥2 Cyrillic letters; a RapidOCR box with
    ≥2 Cyrillic letters is *needed* only when no Cyrillic Tesseract line covers it (otherwise it
    is the same text read twice)."""
    out = []
    cyl = [d for d in P.lines if d["cyr"] >= 2]
    for d in cyl:
        out.append(("tess", d["id"], d["box"], d["text"], True))
    for r in P.rapid:
        if cyr_count(r["text"]) < 2:
            continue
        covered = sum(ov(r["box"], d["box"]) for d in cyl) >= 0.5
        out.append(("rapid", r["i"], r["box"], r["text"], not covered))
    return out


# ---------------------------------------------------------------- volume collection
@dataclass
class Segments:
    segs: list = field(default_factory=list)      # [{id,key,fold,sig,n,need,sample,variants,occ}]
    index: dict = field(default_factory=dict)     # fold of any variant -> seg index
    stats: dict = field(default_factory=dict)

    def find(self, key: str):
        i = self.index.get(fold(key))
        return None if i is None else self.segs[i]

    def save(self, path: Path):
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(version=1, stats=self.stats, segments=self.segs),
                                   ensure_ascii=False, indent=0), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Segments":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        s = cls(segs=d["segments"], stats=d.get("stats", {}))
        for i, g in enumerate(s.segs):
            for v in g["variants"]:
                s.index[v["fold"]] = i
        return s


def pages_of(ctx):
    return sorted(int(p.stem[1:]) for p in (ctx.w / "lines").glob("p*.tsv"))


def collect(ctx, pages=None, fuzzy: bool = True, write: bool = True, skip_notes: bool = True) -> Segments:
    """All Russian OCR readings of the volume -> deduplicated segments (work/seg/segments.json).
    ``skip_notes``: readings inside detected numbered-notes regions are kept but not *needed*
    (page_notes translates those clauses from the image; no phrase_translate items for them)."""
    from scan import packets as K
    groups: dict[str, dict] = {}
    n_tess = n_rapid = n_rapid_need = n_notes = 0
    for pg in pages or pages_of(ctx):
        P = load_page(ctx, pg, words=False)
        items = page_texts(P)
        tl = [dict(box=d["box"], text=d["text"]) for d in P.lines]
        rl = [dict(box=r["box"], text=r["text"]) for r in P.rapid]
        nboxes = [r["box"] for r in K.notes_regions(P, K.title_block(P, getattr(ctx.cfg, "title_code_regex", "")))]             if skip_notes and P.W else []
        members = set()
        if getattr(ctx.cfg, "output_standard", 1) >= 2:      # paragraphs are translated whole
            from scan import prose
            tbb = K.title_block(P, getattr(ctx.cfg, "title_code_regex", ""))
            pars = prose.classify(prose.paragraphs(P.lines),
                                  lambda b: bool(tbb) and tbb[0] <= (b[0] + b[2]) / 2 <= tbb[2] and tbb[1] <= (b[1] + b[3]) / 2 <= tbb[3])
            pars = [q for q in pars if not any(ov(q["box"], nb) >= 0.6 for nb in nboxes)]
            for q in pars:
                members |= set(q["ids"])
                items.append(("para", "P" + "-".join(map(str, q["ids"])), q["box"], q["ru"], True))
        for src, iid, box, text, need in items:
            if src == "tess" and iid in members:
                need = False
            if count_only(text):
                continue
            if need and any(ov(box, nb) >= 0.6 for nb in nboxes):
                need = False; n_notes += 1
            if src == "tess":
                n_tess += 1
                txt, ok = reconcile(text, rapid_over(P, box))
                above, below = neighbours(tl, next(x for x in tl if x["box"] == box))
            elif src == "para":
                txt, ok = reconcile(text, rapid_over(P, box))
                above, below = "", ""
            else:
                n_rapid += 1; n_rapid_need += need
                txt, ok = norm(text), []
                above, below = neighbours(rl, next(x for x in rl if x["box"] == box))
            key, nums, _, _ = make_key(txt)
            if cyr_count(key) < 2:
                continue
            fk = fold(key)
            g = groups.get(fk)
            if g is None:
                g = groups[fk] = dict(fold=fk, keys={}, occ=[], need=False)
            g["keys"][key] = g["keys"].get(key, 0) + 1
            g["need"] |= bool(need)
            g["occ"].append(dict(pg=pg, src=src, id=iid, box=[round(v, 4) for v in box], raw=text,
                                 above=above, below=below, need=bool(need),
                                 nums_ok=(all(ok) if ok else None)))
    # display key per exact group = most frequent cased variant
    exact = []
    for fk, g in groups.items():
        k = max(g["keys"].items(), key=lambda kv: (kv[1], kv[0]))[0]
        exact.append(dict(key=k, fold=fk, sig=sig(k), skel=skeleton(k), n=len(g["occ"]), need=g["need"], occ=g["occ"]))
    exact.sort(key=lambda e: (-e["n"], e["fold"]))
    clusters: list[dict] = []
    buckets: dict[str, list[int]] = {}
    for e in exact:
        target = None
        if fuzzy:
            for ci in buckets.get(e["sig"], []):
                if similar(e["skel"], clusters[ci]["skel"]):
                    target = ci; break
        if target is None:
            buckets.setdefault(e["sig"], []).append(len(clusters))
            clusters.append(dict(key=e["key"], fold=e["fold"], sig=e["sig"], skel=e["skel"], members=[e]))
        else:
            clusters[target]["members"].append(e)
    S = Segments()
    for c in clusters:
        occ = [o for m in c["members"] for o in m["occ"]]
        rep = c["members"][0]
        sample = rep["occ"][0]["raw"] if rep["occ"] else rep["key"]
        g = dict(id=key_id(c["fold"]), key=c["key"], fold=c["fold"], sig=c["sig"], n=len(occ),
                 need=any(m["need"] for m in c["members"]), sample=sample,
                 variants=[dict(key=m["key"], fold=m["fold"], n=m["n"]) for m in c["members"]], occ=occ)
        for m in c["members"]:
            S.index[m["fold"]] = len(S.segs)
        S.segs.append(g)
    S.stats = dict(tess_lines=n_tess, rapid_boxes=n_rapid, rapid_uncovered=n_rapid_need, in_notes=n_notes,
                   unique_keys=len(exact), after_fuzzy=len(clusters),
                   needed_keys=sum(1 for g in S.segs if g["need"]),
                   needed_occ=sum(sum(1 for o in g["occ"] if o["need"]) for g in S.segs))
    if write:
        S.save(ctx.w / "seg" / "segments.json")
    return S
