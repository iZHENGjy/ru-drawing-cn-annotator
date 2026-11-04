"""Pairing RU->EN runs, computing Chinese slots, fitting and drawing."""
from __future__ import annotations
import math, re
from dataclasses import dataclass, field
import pymupdf
from .layout import (Run, Word, digits, needs_replacement, to_frame_rect, from_frame_pt,
                     from_frame_rect)

import os
from pathlib import Path

# Chinese font: $RUDRAW_FONT, project.toml [project] font, or the first one found here.
# Last resort is PyMuPDF's built-in CJK font (Droid Sans Fallback).
FONT_CANDIDATES = [
    "C:/Windows/Fonts/Deng.ttf", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Light.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
]
# Latin/Cyrillic/symbol glyphs the Chinese font may lack (Ø, ⌀, №, ...)
FALLBACK_CANDIDATES = ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
                       "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial Unicode.ttf"]


def _first(paths):
    return next((p for p in paths if Path(p).is_file()), None)


FONT_FILE: str | None = None
FONT: pymupdf.Font
FALLBACK: pymupdf.Font


def set_font(fontfile: str | None = None):
    """Select the Chinese font (None: environment / auto-detect / built-in)."""
    global FONT_FILE, FONT, FALLBACK
    FONT_FILE = fontfile or os.environ.get("RUDRAW_FONT") or _first(FONT_CANDIDATES)
    FONT = pymupdf.Font(fontfile=FONT_FILE) if FONT_FILE else pymupdf.Font("cjk")
    fb = _first(FALLBACK_CANDIDATES)
    FALLBACK = pymupdf.Font(fontfile=fb) if fb else FONT


set_font()


def _segments(text):
    """split text into (chunk, font) by glyph availability"""
    out = []
    for ch in text:
        f = FONT if (FONT.has_glyph(ord(ch)) or not FALLBACK.has_glyph(ord(ch))) else FALLBACK
        if out and out[-1][1] is f:
            out[-1][0] += ch
        else:
            out.append([ch, f])
    return out


def text_len(text, fs):
    return sum(f.text_length(c, fs) for c, f in _segments(text))
COLOR = (0.0, 0.18, 0.75)
NO_LINE_START = set("，。；：、）》」』？！,.;:)%")


# ---------------------------------------------------------------- obstacles
class Obstacles:
    """Text rects + line-art segments, per orientation frame."""
    def __init__(self, page: pymupdf.Page, words: list[Word], segs):
        self.words = list(words)
        self.segs = segs
        self.page_rect = page.rect
        self._cache = {}

    def frame(self, o):
        if o not in self._cache:
            sr = []
            for a, b in self.segs:
                r = to_frame_rect(pymupdf.Rect(a, a) | b, o)
                sr.append(r)
            self._cache[o] = sr
        return self._cache[o]

    def _wf(self, o):
        """cached (id, frame rect) of words for orientation o, refreshed when words grow"""
        key = ("w", o)
        c = self._cache.get(key)
        if c is None or c[0] != len(self.words):
            c = (len(self.words), [(id(w), to_frame_rect(w.rect, o)) for w in self.words])
            self._cache[key] = c
        return c[1]

    def text_hits(self, fr: pymupdf.Rect, o, ignore: set) -> list[pymupdf.Rect]:
        out = []
        x0, y0, x1, y1 = fr.x0, fr.y0, fr.x1, fr.y1
        for wid, r in self._wf(o):
            if r.x0 < x1 and r.x1 > x0 and r.y0 < y1 and r.y1 > y0 and wid not in ignore:
                out.append(r)
        return out

    def seg_hits(self, fr: pymupdf.Rect, o) -> list[pymupdf.Rect]:
        out = []
        x0, y0, x1, y1 = fr.x0, fr.y0, fr.x1, fr.y1
        for r in self.frame(o):
            if r.x1 >= x0 and r.x0 <= x1 and r.y1 >= y0 and r.y0 <= y1:
                out.append(r)
        return out

    def free(self, fr, o, ignore=frozenset(), segs=True) -> bool:
        pr = to_frame_rect(self.page_rect, o)
        if not (fr.x0 >= pr.x0 + 2 and fr.y0 >= pr.y0 + 2 and fr.x1 <= pr.x1 - 2 and fr.y1 <= pr.y1 - 2):
            return False                     # never place anything outside the page
        fr = pymupdf.Rect(fr.x0 + 0.4, fr.y0 + 0.4, fr.x1 - 0.4, fr.y1 - 0.4)   # touching a rule is fine
        if fr.is_empty:
            return False
        if self.text_hits(fr, o, ignore):
            return False
        return not (segs and self.seg_hits(fr, o))


# ---------------------------------------------------------------- pairing
@dataclass(eq=False)
class Unit:
    ru: Run | None
    en: Run | None
    mode: str            # full / inline / insert / keep
    zh: str | None = None
    key: str = ""
    status: str = ""     # ok / missing / overflow / unplaced / skipped
    placed: list = field(default_factory=list)


def _hov(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    return min(a.x1, b.x1) - max(a.x0, b.x0)


def pair_runs(runs: list[Run]) -> list[Unit]:
    ru_runs = [r for r in runs if r.lang == "ru" and r.has("ru")]
    en_runs = [r for r in runs if r.lang == "en" and needs_replacement(r)]
    cands = []
    for e in en_runs:
        er, s = e.frect, e.size
        for r in ru_runs:
            if r.orient != e.orient:
                continue
            rr = r.frect
            score = None
            same_line = _vov(rr, er) > 0.5 * min(rr.height, er.height) and len(r.lines) == 1 and len(e.lines) == 1
            if same_line and -s < er.x0 - rr.x1 < 3 * s:
                score = 95
            elif r.block == e.block and r.lines and e.lines:
                # EN follows RU in the same block (reading order)
                if e.seq > r.seq and er.y0 >= rr.y0 - 0.3 * s and er.y0 - rr.y1 < 2.5 * s:
                    score = 90 - (er.y0 - rr.y1) / s
            elif er.y0 >= rr.y1 - 0.4 * s and er.y0 - rr.y1 < 2.6 * s and (
                    abs(r.size - e.size) < 1 or
                    (abs(r.size - e.size) < 3 and abs((rr.x0 + rr.x1) / 2 - (er.x0 + er.x1) / 2) < 1.5 * s)):
                ov = _hov(rr, er)
                cx = abs((rr.x0 + rr.x1) / 2 - (er.x0 + er.x1) / 2)
                if ov > 0.3 * min(rr.width, er.width) or abs(rr.x0 - er.x0) < 2 * s or cx < 2 * s:
                    score = 80 - (er.y0 - rr.y1) / s - abs(rr.x0 - er.x0) / (10 * s)
            dr, de = digits(r.text), digits(e.text)
            if score is None and len(r.lines) >= 2 and len(e.lines) >= 2 and dr and de:
                # notes blocks: Russian paragraph block, English block further below
                inter = len(set(dr) & set(de)) / len(set(dr) | set(de))
                if (inter >= 0.6 and 0 <= er.y0 - rr.y1 < 12 * s and abs(rr.x0 - er.x0) < 6 * s):
                    score = 60
            if score is None:
                continue
            if dr and de:
                inter = len(set(dr) & set(de)); uni = len(set(dr) | set(de))
                score += 15 * inter / uni - (25 if inter / uni < 0.34 else 0)
            cands.append((score, id(r), id(e), r, e))
    cands.sort(key=lambda c: -c[0])
    used_r, used_e, units = set(), set(), []
    for score, ir, ie, r, e in cands:
        if ir in used_r or ie in used_e or score < 45:
            continue
        used_r.add(ir); used_e.add(ie)
        same_line = _vov(r.frect, e.frect) > 0.5 * min(r.frect.height, e.frect.height)
        mode = "inline" if (len(r.lines) == 1 and len(e.lines) == 1 and same_line) else "full"
        units.append(Unit(r, e, mode))
    # stray English lines directly under a paired English run (same cell): part of it
    paired = [u for u in units if u.en is not None]
    for e in en_runs:
        if id(e) in used_e:
            continue
        er = e.frect
        for u in paired:
            pr = u.en.frect
            if (u.en.orient == e.orient and 0 <= er.y0 - pr.y1 < 0.9 * e.size
                    and _hov(pr, er) > 0.3 * min(pr.width, er.width) and u.mode != "inline"):
                u.en.words.extend(e.words); u.en.lines.extend(e.lines)
                used_e.add(id(e))
                break
    for r in ru_runs:
        if id(r) not in used_r:
            units.append(Unit(r, None, "insert"))
    for e in en_runs:
        if id(e) not in used_e:
            units.append(Unit(None, e, "en_only"))
    return units


def _vov(a, b):
    return min(a.y1, b.y1) - max(a.y0, b.y0)


# ---------------------------------------------------------------- wrapping
import jieba, logging
jieba.setLogLevel(logging.WARNING)
# generic structural / title-block terms that must never be split across two lines;
# project-specific words come from project.toml [segmentation] words
BASE_WORDS = ("二次灌浆层", "现浇基础", "地脚螺栓组", "立体钢筋骨架", "基础短柱", "钢筋骨架", "绑扎钢丝",
              "施工缝", "混凝土垫层", "预埋件", "钢筋制品", "构件编号", "型材牌号", "钢材用量表", "零件表",
              "锚固长度", "焊脚尺寸", "底漆", "防水涂料", "技术要求", "总说明", "柱底板", "定位轴线", "刻痕",
              "螺纹长度", "非标准", "锈蚀", "保护层厚度", "明细表", "抗冻等级", "抗渗等级",
              "标准化审查", "项目总设计师", "主任专家", "室主任", "修改处数", "原件登记号", "代替登记号")


def add_words(words):
    for _t in words:
        jieba.add_word(_t)


add_words(BASE_WORDS)

LATIN_RUN = r"[A-Za-zА-Яа-яЁё0-9⟨⟩.,×xх=+\-/()%°²³Ø№\"«»₁:]+"


def tokens(s: str):
    """Word-level tokens: Latin/Cyrillic/number runs stay whole, Chinese is segmented
    into words with jieba so a line never breaks inside a word."""
    out = []
    for m in re.finditer(LATIN_RUN + r"|\s+|[一-鿿]+|.", s):
        t = m.group()
        if re.fullmatch(r"[一-鿿]+", t):
            out.extend(jieba.lcut(t, HMM=False))
        else:
            out.append(t)
    return out


def wrap(text: str, widths: list[float], fs: float, hard: bool = False) -> list[str] | None:
    """Greedy wrap into lines with given widths. '\n' forces a break. None if it doesn't fit."""
    lines, li, cur = [], 0, ""
    def width(t):
        return text_len(t, fs)
    paras = text.split("\n")
    for pi, para in enumerate(paras):
        toks = tokens(para)
        for t in toks:
            if li >= len(widths):
                return None
            cand = cur + t
            if width(cand.rstrip()) <= widths[li] + 0.01:
                cur = cand
            elif not cur.strip():
                if width(t) > widths[li]:
                    if not hard:
                        return None      # a single word wider than the line: let caller shrink
                    for ch in t:         # last resort only (overflow path)
                        if width(cur + ch) > widths[li] and cur:
                            lines.append(cur); li += 1; cur = ""
                            if li >= len(widths):
                                return None
                        cur += ch
                    continue
                cur = cand
            else:
                if t[:1] in NO_LINE_START and len(t.strip()) <= 2:
                    cur = cand; continue              # punctuation never starts a line: hang it
                lines.append(cur.rstrip()); li += 1; cur = t.lstrip()
                if li >= len(widths):
                    return None
                if width(cur) > widths[li] and not hard:
                    return None
        if pi < len(paras) - 1:
            lines.append(cur.rstrip()); li += 1; cur = ""
    if cur.strip():
        if li >= len(widths):
            return None
        lines.append(cur.rstrip())
    return lines


# ---------------------------------------------------------------- slots
def en_slots(u: Unit, obs: Obstacles, redacted: set) -> list[pymupdf.Rect]:
    e = u.en
    o = e.orient
    s = e.size
    full = e.frect
    slots = []
    for ln in e.lines:
        if u.mode == "inline":                  # only the English words are replaced
            ln = [w for w in ln if w.lang == "en"]
            if not ln:
                continue
        r = pymupdf.Rect(ln[0].frect)
        for w in ln[1:]:
            r |= w.frect
        slots.append(r)
    # extend each slot to the right until an obstacle (text not redacted / vertical line)
    out = []
    # single labels may grow a little to the right; paragraphs stay inside their own box
    cap = full.x1 + (6 * s if len(slots) == 1 else 0)
    pr = to_frame_rect(obs.page_rect, o)
    cap = min(cap, pr.x1 - 2)
    # only this unit's REMOVED words are free space; kept tokens (∅12, 200) and the
    # English of other units (their Chinese goes there) are obstacles
    own = {id(w) for w in e.words if id(w) in redacted}
    for r in slots:
        band = pymupdf.Rect(r.x1, r.y0 + 0.25 * r.height, cap, r.y1 - 0.25 * r.height)
        x_lim = cap
        for t in obs.text_hits(band, o, own):
            x_lim = min(x_lim, t.x0 - 0.3 * s)
        for g in obs.seg_hits(band, o):
            if g.height > 0.4 * r.height:          # vertical-ish line crossing the band
                x_lim = min(x_lim, g.x0 - 0.25 * s)
        out.append(pymupdf.Rect(r.x0, r.y0, max(r.x1, x_lim), r.y1))
    if len(out) > 1:                    # paragraph: every line may use the full paragraph width
        right = max(r.x1 for r in out)
        out = [pymupdf.Rect(r.x0, r.y0, right, r.y1) for r in out]
    return out


def extra_slots_below(last: pymupdf.Rect, pitch: float, o, obs: Obstacles, redacted: set, n=3):
    out = []
    r = last
    for _ in range(n):
        r = pymupdf.Rect(r.x0, r.y0 + pitch, r.x1, r.y1 + pitch)
        probe = pymupdf.Rect(r.x0, r.y0 + 0.1 * r.height, r.x1, r.y1 - 0.1 * r.height)
        if not obs.free(probe, o, redacted):
            break
        out.append(r)
    return out


def fit(text: str, slots: list[pymupdf.Rect], base: float, steps=(1.0, 0.93, 0.86, 0.8, 0.74, 0.68, 0.62, 0.56, 0.5)):
    # consecutive slots closer than one Chinese line (e.g. fractions): cap the size
    cap = base
    for a, b in zip(slots, slots[1:]):
        pitch = (b.y0 + b.y1) / 2 - (a.y0 + a.y1) / 2
        if 0 < pitch:
            cap = min(cap, pitch / 1.08)
    for k in steps:
        fs = min(base * k, cap)
        lines = wrap(text, [r.width for r in slots], fs)
        if lines is not None:
            return fs, lines
    return None, None


# ---------------------------------------------------------------- drawing
class Painter:
    def __init__(self, page: pymupdf.Page):
        self.page = page
        self.writers = {}
        self.drawn = []          # (page rect of the written line, text)

    def line(self, text: str, slot: pymupdf.Rect, fs: float, o: str):
        if not text:
            return
        asc, desc = FONT.ascender, FONT.descender
        h = (asc - desc) * fs
        base_y = slot.y0 + (slot.height - h) / 2 + asc * fs
        X, Y = slot.x0, base_y
        fr = pymupdf.Rect(X, base_y - asc * fs * 0.85, X + text_len(text, fs), base_y - desc * fs * 0.5)
        self.drawn.append((from_frame_rect(fr, o), text))
        px, py = from_frame_pt(X, Y, o)
        rot = {"h": 0, "up": 90, "down": 270}[o]
        if rot == 0:
            tw = self.writers.setdefault(rot, pymupdf.TextWriter(self.page.rect, color=COLOR))
            pos = pymupdf.Point(px, py)
            for chunk, f in _segments(text):
                _, pos = tw.append(pos, chunk, font=f, fontsize=fs)
                pos = pymupdf.Point(pos)
        else:
            # rotated text: native insert_text, point = start of the baseline
            if FONT_FILE is None:            # built-in CJK font
                self.page.insert_text((px, py), text, fontname="china-s", fontsize=fs, rotate=rot, color=COLOR)
                return
            if not getattr(self, "_font_in", False):
                self.page.insert_font(fontname="zhfont", fontfile=FONT_FILE)
                self._font_in = True
            self.page.insert_text((px, py), text, fontname="zhfont", fontsize=fs, rotate=rot, color=COLOR)

    def flush(self):
        for rot, tw in self.writers.items():
            tw.write_text(self.page, color=COLOR)
        self.writers = {}
