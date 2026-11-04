"""Layout analysis for Russian (and bilingual RU/EN) drawing PDFs.

All analysis happens in a *frame* coordinate system in which the text of the
orientation group runs left-to-right (x right, y down).  Horizontal text uses the
identity frame; upward-running text (dir = (0,-1)) uses (X, Y) = (-y, x).

  chars -> words -> lines -> blocks -> runs (RU / EN) -> pairs
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
import pymupdf

CYR = re.compile(r"[А-Яа-яЁё]")
CYR_LOW2 = re.compile(r"[а-яё]{2,}")
LAT2 = re.compile(r"[A-Za-z]{2,}")
STD_TOKENS = {"ГОСТ", "ГОСТР", "СП", "СНиП", "ТУ", "СТО", "ПБ", "ФЗ", "GOST", "SP", "SNiP", "TU"}
UNIT_EN = {"kg", "t", "m", "m2", "m3", "m²", "m³", "mm", "cm", "kN", "MPa", "kPa"}

# Personal names (title-block signatures) and organisation codes are kept verbatim.
# They are project data: fill them from project.toml ([keep] names / acronyms).
NAMES: set[str] = set()
RU_UNITS = {"мм", "м", "см", "км", "кг", "т", "м2", "м3", "м²", "м³", "кН", "МПа", "кПа", "л"}
# legal-form abbreviations and common drawing codes that are never translated
ACRONYMS = {"Скв", "XPS", "PJSC", "JSC", "LLC", "ПАО", "АО", "ООО", "НПП", "ЗАО", "ОАО"}


# ---------------------------------------------------------------- line-art index
class SegIndex:
    """Near-vertical and near-horizontal line segments in a given frame, bucketed."""
    def __init__(self, segs_page, o, bucket=20.0):
        self.v, self.h, self.b = {}, {}, bucket
        for a, b in segs_page:
            r = to_frame_rect(pymupdf.Rect(a, a) | b, o)
            if r.width < 1.0 and r.height > 2:
                for k in range(int(r.x0 // bucket), int(r.x1 // bucket) + 1):
                    self.v.setdefault(k, []).append(r)
            elif r.height < 1.0 and r.width > 2:
                for k in range(int(r.y0 // bucket), int(r.y1 // bucket) + 1):
                    self.h.setdefault(k, []).append(r)

    def vline_between(self, x0, x1, y0, y1) -> bool:
        """vertical segment with x in (x0,x1) spanning most of [y0,y1]"""
        need = 0.6 * (y1 - y0)
        for k in range(int(x0 // self.b), int(x1 // self.b) + 1):
            for r in self.v.get(k, ()):
                if x0 < r.x0 < x1 and min(r.y1, y1) - max(r.y0, y0) >= need:
                    return True
        return False

    def hline_between(self, y0, y1, x0, x1) -> bool:
        need = 0.5 * (x1 - x0)
        for k in range(int(y0 // self.b), int(y1 // self.b) + 1):
            for r in self.h.get(k, ()):
                if y0 < r.y0 < y1 and min(r.x1, x1) - max(r.x0, x0) >= min(need, 8):
                    return True
        return False


def page_segments(page: pymupdf.Page):
    segs = []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                segs.append((it[1], it[2]))
            elif it[0] == "re":
                r = it[1]
                if r.width < 0.6 or r.height < 0.6:
                    segs.append((r.tl, r.br)); continue
                segs += [(r.tl, r.tr), (r.tr, r.br), (r.br, r.bl), (r.bl, r.tl)]
            elif it[0] == "qu":
                q = it[1]
                segs += [(q.ul, q.ur), (q.ur, q.lr), (q.lr, q.ll), (q.ll, q.ul)]
    return segs


# ---------------------------------------------------------------- frames
ORIENTS = {"h": (1.0, 0.0), "up": (0.0, -1.0), "down": (0.0, 1.0)}


def orient_of(d) -> str | None:
    for k, v in ORIENTS.items():
        if abs(d[0] - v[0]) < 0.05 and abs(d[1] - v[1]) < 0.05:
            return k
    return None


def to_frame_pt(x, y, o):
    if o == "h":
        return x, y
    if o == "up":
        return -y, x
    return y, -x          # down


def from_frame_pt(X, Y, o):
    if o == "h":
        return X, Y
    if o == "up":
        return Y, -X
    return -Y, X


def to_frame_rect(r: pymupdf.Rect, o) -> pymupdf.Rect:
    a = to_frame_pt(r.x0, r.y0, o); b = to_frame_pt(r.x1, r.y1, o)
    return pymupdf.Rect(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def from_frame_rect(r: pymupdf.Rect, o) -> pymupdf.Rect:
    a = from_frame_pt(r.x0, r.y0, o); b = from_frame_pt(r.x1, r.y1, o)
    return pymupdf.Rect(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


# ---------------------------------------------------------------- data
@dataclass(eq=False)
class Word:
    text: str
    rect: pymupdf.Rect          # page coords
    size: float
    lang: str = ""              # ru / en / neu
    frect: pymupdf.Rect | None = None   # frame coords
    chars: list = field(default_factory=list)   # [(c, Rect)] page coords


@dataclass(eq=False)
class Line:
    words: list
    size: float
    orient: str
    @property
    def frect(self):
        r = pymupdf.Rect(self.words[0].frect)
        for w in self.words[1:]:
            r |= w.frect
        return r
    @property
    def text(self):
        return " ".join(w.text for w in self.words)


@dataclass(eq=False)
class Run:
    lang: str
    orient: str
    block: int
    words: list = field(default_factory=list)
    lines: list = field(default_factory=list)      # list[list[Word]]
    _li: int = -1
    seq: int = -1
    @property
    def text(self):
        return "\n".join(" ".join(w.text for w in l) for l in self.lines)
    @property
    def frect(self):
        r = pymupdf.Rect(self.words[0].frect)
        for w in self.words[1:]:
            r |= w.frect
        return r
    @property
    def size(self):
        return max(w.size for w in self.words)
    def has(self, lang):
        return any(w.lang == lang for w in self.words)


# ---------------------------------------------------------------- word language
def word_lang(t: str) -> str:
    core = t.strip("()[]:;,.«»\"'-–—/")
    if not core:
        return "neu"
    if core in STD_TOKENS or core in NAMES or core in ACRONYMS or core in RU_UNITS:
        return "neu"
    has_c, has_l = bool(CYR.search(core)), bool(LAT2.search(core))
    has_d = any(ch.isdigit() for ch in core)
    lower_c = bool(re.search(r"[а-яё]", core))
    if has_d and not re.search(r"[а-яёa-z]{2,}", core):      # 16-А500C, KП2, 1,2-О-Ч, Фм1, L=2550
        return "neu"
    if re.match(r"^[А-ЯЁ][а-я]?\d", core):                    # Фм1, Фм6.1
        return "neu"
    if has_c and has_l:
        return "mix"
    if has_c:
        if len(core) == 1 and core.isupper():                 # axis label С, Д
            return "neu"
        if not lower_c and len(core) <= 3 and "." not in t:  # КП, БФ, ИГЭ-like codes
            return "neu"
        return "ru"
    if has_l:
        if re.match(r"^[A-Z]{1,3}[\d.,\-]+\w*$", core) or (len(core) == 1):
            return "neu"
        return "en" if LAT2.search(core) else "neu"
    return "neu"


def split_mixed(w: Word) -> list[Word]:
    """'кг/kg' -> ['кг/', 'kg'] ; '№/Sup.' etc."""
    m = re.match(r"^(.*[А-Яа-яЁё][^A-Za-z]*?/)\s*([A-Za-z].*)$", w.text)
    if not m:
        m2 = re.match(r"^([A-Za-z][^/]*?/)\s*(.*[А-Яа-яЁё].*)$", w.text)
        if not m2:
            w.lang = "ru"
            return [w]
        a, b = m2.group(1), m2.group(2)
        la, lb = "en", word_lang(b)
    else:
        a, b = m.group(1), m.group(2)
        la, lb = "ru", word_lang(b)
    k = len(a)
    ca, cb = w.chars[:k], w.chars[k:]
    if not ca or not cb:
        w.lang = "ru"
        return [w]
    def mk(text, chars, lang):
        r = pymupdf.Rect(chars[0][1])
        for _, cr in chars[1:]:
            r |= cr
        return Word(text, r, w.size, lang, chars=chars)
    return [mk("".join(c for c, _ in ca), ca, la), mk("".join(c for c, _ in cb), cb, lb if lb != "mix" else "ru")]


# ---------------------------------------------------------------- extraction
def _is_dup(w, seen):
    key = (w.text, round(w.rect.x0), round(w.rect.y0))
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for r in seen.get((w.text, key[1] + dx, key[2] + dy), ()):
                if (r & w.rect).get_area() > 0.8 * w.rect.get_area():
                    return True
    seen.setdefault(key, []).append(w.rect)
    return False


def extract_lines(page: pymupdf.Page, segidx: dict):
    raw = page.get_text("rawdict")
    lines, other = [], []
    seen = {}
    for b in raw["blocks"]:
        for l in b.get("lines", []):
            o = orient_of(l["dir"])
            words, cur, rect, size, chs = [], "", None, 0.0, []
            for s in l["spans"]:
                for ch in s["chars"]:
                    c = ch["c"]
                    if c.isspace():
                        if cur:
                            words.append(Word(cur, rect, size, chars=chs)); cur, rect, chs = "", None, []
                        continue
                    r = pymupdf.Rect(ch["bbox"])
                    cur += c; rect = r if rect is None else rect | r; size = s["size"]
                    chs.append((c, r))
            if cur:
                words.append(Word(cur, rect, size, chars=chs))
            if not words:
                continue
            out = []
            for w in words:
                w.lang = word_lang(w.text)
                out.extend(split_mixed(w) if w.lang == "mix" else [w])
            # CAD exports sometimes draw the same text twice on top of itself: keep one copy
            # (glyph-centre redaction still removes both copies of English)
            out = [w for w in out if not _is_dup(w, seen)]
            if not out:
                continue
            if o is None:
                other.append(out); continue
            for w in out:
                w.frect = to_frame_rect(w.rect, o)
            # split at large horizontal gaps (separate table cells on one baseline)
            out.sort(key=lambda w: w.frect.x0)
            cur = [out[0]]
            si = segidx[o]
            for w in out[1:]:
                p = cur[-1].frect
                y0 = min(p.y0, w.frect.y0); y1 = max(p.y1, w.frect.y1)
                if (w.frect.x0 - p.x1 > 1.0 * max(w.size, cur[-1].size)
                        or si.vline_between(p.x1 - 0.5, w.frect.x0 + 0.5, y0, y1)):
                    lines.append(Line(cur, max(x.size for x in cur), o)); cur = [w]
                else:
                    cur.append(w)
            lines.append(Line(cur, max(x.size for x in cur), o))
    return lines, other


def merge_fragments(lines: list[Line], si: "SegIndex") -> list[Line]:
    """Join line fragments that sit on (almost) the same baseline right next to each
    other - PDF writers split a line at sub/superscripts (F₁200, m³)."""
    lines = sorted(lines, key=lambda l: (l.frect.x0))
    out: list[Line] = []
    for l in lines:
        lr = l.frect
        best = None
        for m in out:
            mr = m.frect
            vov = min(lr.y1, mr.y1) - max(lr.y0, mr.y0)
            gap = lr.x0 - mr.x1
            # sub/superscript fragments touch the text; captions side by side keep a gap
            if vov >= 0.5 * min(lr.height, mr.height) and -0.5 < gap < 0.6 * max(l.size, m.size) \
                    and not si.vline_between(mr.x1 - 0.5, lr.x0 + 0.5, max(lr.y0, mr.y0), min(lr.y1, mr.y1)):
                best = m
                break
        if best is None:
            out.append(l)
        else:
            best.words.extend(l.words)
            best.size = max(best.size, l.size)
    return out


def group_blocks(lines: list[Line], si: "SegIndex") -> list[list[Line]]:
    lines = sorted(lines, key=lambda l: (l.frect.y0, l.frect.x0))
    blocks: list[list[Line]] = []
    for l in lines:
        lr = l.frect
        best = None
        for bi in range(len(blocks) - 1, max(-1, len(blocks) - 80), -1):
            last = blocks[bi][-1]
            if abs(last.size - l.size) > 1.1:
                continue
            Lr = last.frect
            gap = lr.y0 - Lr.y1
            if gap < -0.3 * l.size or gap > 0.95 * l.size:
                continue
            first = blocks[bi][0].frect
            left_ok = (abs(lr.x0 - first.x0) < 2.2 * l.size or abs(lr.x0 - Lr.x0) < 2.2 * l.size
                       or (0 < Lr.x0 - lr.x0 < 6 * l.size and Lr.width > 15 * l.size))   # first-line indent
            ov = min(lr.x1, Lr.x1) - max(lr.x0, Lr.x0)
            if left_ok or ov > 0.5 * min(lr.width, Lr.width):
                # never merge across a table rule
                if si.hline_between(Lr.y1 - 0.5 * l.size, lr.y0 + 0.5 * l.size,
                                    max(lr.x0, Lr.x0), min(lr.x1, Lr.x1) if ov > 0 else max(lr.x0, Lr.x0) + 1):
                    continue
                pure_new = not any(w.lang != "neu" for w in l.words)
                pure_last = not any(w.lang != "neu" for w in last.words)
                # pure number / name lines stay on their own - except the tail of a long
                # paragraph line ("... опорной плиты" + "45°.")
                tail_of_par = (pure_new and not pure_last and Lr.width > 15 * l.size
                               and (abs(lr.x0 - first.x0) < 2.2 * l.size or 0 < Lr.x0 - lr.x0 < 6 * l.size))
                if (pure_new or pure_last) and not tail_of_par:
                    continue
                best = bi
                break
        if best is None:
            blocks.append([l])
        else:
            blocks[best].append(l)
    return blocks


def runs_of_block(block: list[Line], bi: int, orient: str) -> list[Run]:
    stream = [(li, w) for li, l in enumerate(block) for w in l.words]
    eff = []
    for i, (li, w) in enumerate(stream):
        if w.lang in ("ru", "en"):
            eff.append(w.lang); continue
        nxt = next((x[1].lang for x in stream[i + 1:] if x[0] == li and x[1].lang in ("ru", "en")), None)
        prv = eff[-1] if eff else None
        line_start = i == 0 or stream[i - 1][0] != li
        eff.append(nxt if (line_start and nxt) else (prv or nxt or "neu"))
    # smoothing: a short island (<=2 words) of one language inside the other language
    # (e.g. a quoted Russian code "по" inside an English note) joins its surroundings
    segs = []
    for i, lg in enumerate(eff):
        if segs and segs[-1][0] == lg:
            segs[-1][2] = i
        else:
            segs.append([lg, i, i])
    for k in range(1, len(segs) - 1):
        lg, a, b = segs[k]
        if lg in ("ru", "en") and b - a + 1 <= 2 and segs[k - 1][0] == segs[k + 1][0] != lg \
                and segs[k - 1][0] in ("ru", "en") and (segs[k - 1][2] - segs[k - 1][1]) + (segs[k + 1][2] - segs[k + 1][1]) >= 3:
            for i in range(a, b + 1):
                eff[i] = segs[k - 1][0]
    runs: list[Run] = []
    for (li, w), lg in zip(stream, eff):
        if lg == "neu" and runs:
            lg = runs[-1].lang
        if not runs or runs[-1].lang != lg:
            runs.append(Run(lg, orient, bi))
        r = runs[-1]
        r.words.append(w)
        if not r.lines or r._li != li:
            r.lines.append([]); r._li = li
        r.lines[-1].append(w)
    return runs


def page_words(page: pymupdf.Page):
    """All words of the page (fast path for QA, no line-art analysis)."""
    class _NoSeg:
        def vline_between(self, *a): return False
        def hline_between(self, *a): return False
    lines, other = extract_lines(page, {o: _NoSeg() for o in ORIENTS})
    return [w for l in lines for w in l.words] + [w for ws in other for w in ws]


def analyse(page: pymupdf.Page, segs=None):
    """Return (runs, all_words, other_lines, segs). Runs of all orientations."""
    segs = page_segments(page) if segs is None else segs
    segidx = {o: SegIndex(segs, o) for o in ORIENTS}
    lines, other = extract_lines(page, segidx)
    runs, all_words, bi0 = [], [], 0
    for o in ORIENTS:
        ls = merge_fragments([l for l in lines if l.orient == o], segidx[o])
        blocks = group_blocks(ls, segidx[o])
        for bi, b in enumerate(blocks):
            for r in runs_of_block(b, bi0 + bi, o):
                r.seq = len(runs)
                runs.append(r)
        bi0 += len(blocks)
        for l in ls:
            all_words.extend(l.words)
    for ws in other:
        all_words.extend(ws)
    return runs, all_words, other, segs


def digits(s: str) -> list[str]:
    return sorted(re.findall(r"\d+(?:[.,]\d+)?", s.replace(",", ".")))


def needs_replacement(en: Run) -> bool:
    """EN run that carries English words other than bare units."""
    return any(w.lang == "en" and w.text.strip("/.,;:()") not in UNIT_EN for w in en.words)
