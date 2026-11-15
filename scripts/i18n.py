"""Language pairs: what is source script, which OCR models, fonts, line breaking, prompts, glossaries.

The project sets the pair in ``project.toml``::

    [project]
    source_lang = "ru"     # ISO 639-1: ru en kk uk zh de fr ...
    target_lang = "zh"

Default ``ru -> zh`` keeps every pattern, prompt, font and glossary exactly as before (the approved
layout stays byte-identical).  Every config loader calls :func:`activate`; modules read the active
pair through :data:`PAIR` or through lazy regexes made with :func:`rx`.

``rx(pattern)`` takes a pattern written for Russian (character classes like ``[А-Яа-яЁё]``,
``[а-яё]``, ``[А-ЯЁ]``) and, for another source language, swaps those class contents for the
source alphabet.  For Russian the pattern is compiled unchanged.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HAN = "\u4e00-\u9fff"          # same range as the old [一-鿿]

# CJK fonts (the old engine list, order kept) and fonts for Latin / Cyrillic targets
CJK_FONTS = [
    "C:/Windows/Fonts/Deng.ttf", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Light.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
]
LATIN_FONTS = [
    "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/calibri.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
]


@dataclass(frozen=True)
class Lang:
    code: str
    name: str                    # English name (prompts)
    name_zh: str                 # Chinese name (reports)
    letters: str                 # regex class content of the alphabet
    lower: str                   # lower-case letters (Han: all Han, no case)
    upper: str
    script: str                  # cyrl | latn | hani
    tess: str                    # Tesseract -l
    rapid: str                   # rapidocr LangRec member
    spaces: bool = True          # words separated by spaces (False: CJK, wrap per character / jieba)
    std: str = ""                # standard designations of the source country (regex alternatives)
    tb_anchors: tuple = ()       # title-block labels of the national drawing standard
    months: dict = field(default_factory=dict)


RU_STD = "ГОСТ|GOST|СНиП|СП|SP|ТУ|TU"
PROFILES = {
    "ru": Lang("ru", "Russian", "俄文", "А-Яа-яЁё", "а-яё", "А-ЯЁ", "cyrl", "rus+eng", "ESLAV", std=RU_STD,
               tb_anchors=("Изм.", "Разраб.", "Стадия", "Листов", "Формат", "Утвердил", "Утв.",
                           "Н.контр.", "Кол.уч.", "ГИП", "Нач.отд.", "Пров."),
               months={"январ": "1", "феврал": "2", "март": "3", "апрел": "4", "мая": "5", "май": "5", "июн": "6",
                       "июл": "7", "август": "8", "сентябр": "9", "октябр": "10", "ноябр": "11", "декабр": "12"}),
    "uk": Lang("uk", "Ukrainian", "乌克兰文", "А-Яа-яЁёЄєІіЇїҐґ", "а-яёєіїґ", "А-ЯЁЄІЇҐ", "cyrl", "ukr+eng", "CYRILLIC",
               std="ДСТУ|ДБН|ГОСТ|GOST|DSTU|DBN"),
    "kk": Lang("kk", "Kazakh", "哈萨克文", "А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі", "а-яёәғқңөұүһі", "А-ЯЁӘҒҚҢӨҰҮҺІ", "cyrl",
               "kaz+rus+eng", "CYRILLIC", std="ҚР ҚН|ҚР ЕЖ|СН РК|СП РК|ГОСТ|GOST|СНиП|СП|SP|ТУ|TU",
               tb_anchors=("Изм.", "Разраб.", "Стадия", "Листов", "Формат", "Утвердил", "Н.контр.", "ГИП", "Пров.")),
    "en": Lang("en", "English", "英文", "A-Za-z", "a-z", "A-Z", "latn", "eng", "EN",
               std="BS EN|BS|EN|ISO|ASTM|ACI|AISC|ANSI|ASME|API|DIN",
               tb_anchors=("Drawn", "Checked", "Approved", "Scale", "Sheet", "Rev.", "Date", "Title", "Drawing No."),
               months={"jan": "1", "feb": "2", "mar": "3", "apr": "4", "may": "5", "jun": "6", "jul": "7",
                       "aug": "8", "sep": "9", "oct": "10", "nov": "11", "dec": "12"}),
    "de": Lang("de", "German", "德文", "A-Za-zÄÖÜäöüß", "a-zäöüß", "A-ZÄÖÜ", "latn", "deu+eng", "LATIN",
               std="DIN EN|DIN|EN|ISO"),
    "fr": Lang("fr", "French", "法文", "A-Za-zÀ-ÖØ-öø-ÿŒœ", "a-zß-öø-ÿœ", "A-ZÀ-ÖØ-ÞŒ", "latn", "fra+eng", "LATIN",
               std="NF EN|NF|EN|ISO"),
    "zh": Lang("zh", "Simplified Chinese", "中文", HAN, HAN, HAN, "hani", "chi_sim+eng", "CH", spaces=False,
               std="GB/T|GB|JGJ|JG/T|CECS|DL/T|SH/T|SH|HG/T",
               tb_anchors=("设计", "校对", "审核", "审定", "批准", "比例", "图号", "日期", "工程名称")),
}

# glossary folders shipped for a pair; the default ru-zh keeps the historical file names
GLOSSARY_BASE = {"ru-zh": ["gost_general.json", "spds_sheet_frame.json"],
                 "en-zh": ["general.json", "title_block.json"],
                 "zh-en": ["general.json", "title_block.json"],
                 "zh-ru": ["general.json"],
                 "ru-en": ["general.json"],
                 "kk-zh": ["general.json"]}

# words of the target language the engines write themselves (legends, notes headings)
UI = {
    "zh": {"tb_legend": "图签译文：", "notes": "说明", "low_conf": "虚线下划线 = 原文识别存疑,请核对", "see": "译文见右侧",
           "sheet_header": "页"},
    "en": {"tb_legend": "Title block: ", "notes": "Notes", "low_conf": "dotted underline = source text uncertain, check",
           "see": "translation on the right", "sheet_header": "Sheet"},
    "ru": {"tb_legend": "Штамп: ", "notes": "Примечания", "low_conf": "пунктир = исходный текст распознан неуверенно",
           "see": "перевод справа", "sheet_header": "Лист"},
}


@dataclass
class Pair:
    src: Lang
    tgt: Lang

    @property
    def key(self) -> str:
        return f"{self.src.code}-{self.tgt.code}"

    @property
    def default(self) -> bool:
        return self.key == "ru-zh"

    # ---- OCR
    @property
    def tess_lang(self) -> str:
        return self.src.tess

    @property
    def rapid_lang(self) -> str:
        return self.src.rapid

    @property
    def lookalike(self) -> bool:
        """Latin <-> Cyrillic look-alike repair only for Cyrillic sources."""
        return self.src.script == "cyrl"

    @property
    def second_latin(self) -> bool:
        """Latin words are a second (English) language of a bilingual drawing — not for Latin sources
        and not when the target itself is English."""
        return self.src.script != "latn" and self.tgt.code != "en"

    # ---- target side
    @property
    def fonts(self) -> list[str]:
        return CJK_FONTS if self.tgt.script == "hani" else LATIN_FONTS

    @property
    def fallback_fonts(self) -> list[str]:
        """glyphs the main font may lack: Latin/symbols for a CJK target, CJK for copied Chinese codes."""
        if self.tgt.script == "hani":
            return ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial Unicode.ttf"]
        return CJK_FONTS if self.src.script == "hani" else LATIN_FONTS[1:]

    @property
    def jieba(self) -> bool:
        return self.tgt.code == "zh"

    def ui(self, key: str) -> str:
        return UI.get(self.tgt.code, UI["en"]).get(key, UI["en"][key])

    # ---- glossaries / prompts
    def glossary_dir(self) -> Path:
        return REPO / "glossary" / self.key

    def base_glossaries(self) -> list[Path]:
        d = self.glossary_dir()
        names = GLOSSARY_BASE.get(self.key) or sorted(p.name for p in d.glob("*.json"))
        return [d / n for n in names]

    def prompt_vars(self) -> dict:
        return {"SRC": self.src.name, "TGT": self.tgt.name, "SRC_CODE": self.src.code, "TGT_CODE": self.tgt.code,
                "STD": self.src.std.replace("|", ", ") or "national standards"}


def _lang(code: str) -> Lang:
    code = (code or "").strip().lower()
    if code not in PROFILES:
        raise ValueError(f"unsupported language {code!r}; known: {', '.join(sorted(PROFILES))}")
    return PROFILES[code]


PAIR = Pair(PROFILES["ru"], PROFILES["zh"])
_GEN = 0          # bumped on every activate(): lazy regexes recompile


def activate(src: str | None = None, tgt: str | None = None) -> Pair:
    """Set the active pair (None = keep / default).  $RUDRAW_PAIR="en-zh" wins when the config is silent."""
    global PAIR, _GEN
    env = os.environ.get("RUDRAW_PAIR", "")
    if not src and not tgt and env:
        src, _, tgt = env.partition("-")
    s, t = _lang(src or "ru"), _lang(tgt or "zh")
    if s.code == t.code:
        raise ValueError(f"source and target language are both {s.code!r}")
    if (s.code, t.code) != (PAIR.src.code, PAIR.tgt.code):
        PAIR = Pair(s, t)
        _GEN += 1
    return PAIR


GUARD: dict = {}      # [guard] table: mark_regex, std_regex (code guards, any language)


def from_config(raw: dict) -> Pair:
    global GUARD, _GEN
    p = (raw or {}).get("project", {}) or {}
    g = dict((raw or {}).get("guard", {}) or {})
    if g != GUARD:
        GUARD = g
        _GEN += 1
    return activate(p.get("source_lang"), p.get("target_lang"))


def guard(key: str) -> str:
    """[guard] value ('' when not set).  mark_regex needs two groups: letters, number."""
    return str(GUARD.get(key) or "")


def pair() -> Pair:
    return PAIR


# ------------------------------------------------------------------ lazy regexes
_RU_CLASSES = [("А-Яа-яЁё", "letters"), ("а-яА-ЯёЁ", "letters"), ("а-яё", "lower"), ("А-ЯЁ", "upper"),
               ("а-я", "lower"), ("А-Я", "upper")]


def adapt(pattern: str, p: Pair | None = None) -> str:
    """Pattern written for Russian -> pattern for the active source alphabet."""
    p = p or PAIR
    if p.src.code == "ru":
        return pattern
    out, i = [], 0
    while i < len(pattern):
        for ru, attr in _RU_CLASSES:
            if pattern.startswith(ru, i):
                out.append(getattr(p.src, attr)); i += len(ru); break
        else:
            out.append(pattern[i]); i += 1
    s = "".join(out)
    return s.replace("[]", "(?!)")


class _Lazy:
    """re.Pattern look-alike compiled for the active pair on first use (and again after activate)."""
    __slots__ = ("_pat", "_flags", "_fn", "_gen", "_rx")

    def __init__(self, pat, flags=0, fn=None):
        self._pat, self._flags, self._fn, self._gen, self._rx = pat, flags, fn, -1, None

    def _get(self):
        if self._gen != _GEN or self._rx is None:
            pat = self._fn(PAIR) if self._fn else adapt(self._pat)
            self._rx = re.compile(pat, self._flags)
            self._gen = _GEN
        return self._rx

    def __getattr__(self, name):
        if name.startswith("__") or name in _Lazy.__slots__:
            raise AttributeError(name)
        return getattr(self._get(), name)

    def __repr__(self):
        return f"i18n.rx({self._get().pattern!r})"


def rx(pattern: str, flags: int = 0) -> _Lazy:
    """Lazy regex for a pattern written with Russian classes (see :func:`adapt`)."""
    return _Lazy(pattern, flags)


def rx_fn(fn, flags: int = 0) -> _Lazy:
    """Lazy regex built by ``fn(pair) -> pattern``."""
    return _Lazy(None, flags, fn)


# source script characters / runs, target script check
SRC_CHAR = rx(r"[А-Яа-яЁё]")
SRC_RUN = rx(r"[А-Яа-яЁё]+")
TGT_CHAR = rx_fn(lambda p: f"[{p.tgt.letters}]")                                   # Chinese: [一-鿿]
TGT_CHAR_X = rx_fn(lambda p: f"[{p.tgt.letters}]" if p.tgt.script != "hani" else "[㐀-鿿]")   # + CJK ext. A


def run_letters(p: Pair | None = None) -> str:
    """letter classes of source and target that form unbreakable words (not CJK, not plain Latin)."""
    p = p or PAIR
    out = []
    for l in (p.src, p.tgt):
        if l.script != "hani" and l.letters != "A-Za-z" and l.letters not in out:
            out.append(l.letters.replace("A-Za-z", ""))
    return "".join(out)


def has_target(s: str) -> bool:
    """the text contains target-language writing (Chinese characters; for an alphabetic target two letters)."""
    s = s or ""
    if PAIR.tgt.script == "hani":
        return bool(TGT_CHAR_X.search(s))
    return len(re.findall(f"[{PAIR.tgt.letters}]", s)) >= 2


def untranslated(word: str) -> bool:
    """a copied source-script run that is a word rather than a code."""
    if PAIR.src.script == "hani":
        return True
    return sum(c.islower() for c in word) >= 3


def count_target(s: str) -> int:
    """number of target characters (CJK characters; letters for an alphabetic target)."""
    return len(TGT_CHAR.findall(s or ""))


# ------------------------------------------------------------------ `re` for source-language patterns
class _ReSrc:
    """Drop-in for the ``re`` module in modules whose patterns are written for Russian:
    ``from i18n import re_src as re``.  For ru the stdlib functions get the patterns unchanged;
    for another source the Russian letter classes are swapped (see :func:`adapt`).
    ``compile`` returns a lazy pattern, so module-level constants follow the pair activated later."""

    def __getattr__(self, name):            # flags, escape, Match, error ...
        return getattr(re, name)

    @staticmethod
    def _p(pattern):
        if isinstance(pattern, _Lazy):
            return pattern._get()
        if isinstance(pattern, str) and PAIR.src.code != "ru":
            return adapt(pattern)
        return pattern

    def compile(self, pattern, flags=0):
        if isinstance(pattern, (_Lazy, re.Pattern)):
            return pattern
        return _Lazy(pattern, flags)

    def search(self, p, s, flags=0): return re.search(self._p(p), s, flags)
    def match(self, p, s, flags=0): return re.match(self._p(p), s, flags)
    def fullmatch(self, p, s, flags=0): return re.fullmatch(self._p(p), s, flags)
    def findall(self, p, s, flags=0): return re.findall(self._p(p), s, flags)
    def finditer(self, p, s, flags=0): return re.finditer(self._p(p), s, flags)
    def split(self, p, s, maxsplit=0, flags=0): return re.split(self._p(p), s, maxsplit=maxsplit, flags=flags)
    def sub(self, p, repl, s, count=0, flags=0): return re.sub(self._p(p), repl, s, count=count, flags=flags)
    def subn(self, p, repl, s, count=0, flags=0): return re.subn(self._p(p), repl, s, count=count, flags=flags)


re_src = _ReSrc()


# member marks (Км1.2, МН10 on ГОСТ drawings): two groups, letters + number.  [guard] mark_regex sets
# them for any language (e.g. '([A-Z]{1,3})-?(\d+(?:\.\d+)?)' for B1, C2.1); none by default outside ru.
MARK_RU = r"([А-ЯЁ]м|МН)\s?(\d+(?:\.\d+)?(?:-\d+)?)"
MARK = rx_fn(lambda p: guard("mark_regex") or (MARK_RU if p.src.code == "ru" else r"(?!)(x)(x)"))
