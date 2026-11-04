"""Project configuration.

Every project-specific value (input files, title-block labels, personal names that
must never be translated, extra segmentation words ...) lives in a small TOML file,
by default ``project.toml`` in the current directory.  Relative paths in the file are
resolved against the directory that contains it.  See ``project.example.toml``.
"""
from __future__ import annotations
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Labels of a ГОСТ 21.101 / ГОСТ Р 21.101 title block (основная надпись).  Their
# bounding box marks the title-block area, where short words get title-block
# translations (context "tb", e.g. «Лист» = sheet number rather than steel plate).
DEFAULT_TB_ANCHORS = ["Изм.", "Разраб.", "Стадия", "Листов", "Формат", "Утвердил", "Утв.",
                      "Н.контр.", "Кол.уч.", "ГИП", "Нач.отд.", "Пров."]


@dataclass
class Config:
    root: Path = field(default_factory=Path.cwd)
    name: str = "project"
    pdf: Path | None = None
    dxf: Path | None = None
    tm: Path = Path("dict/tm.json")
    glossaries: list[Path] = field(default_factory=list)
    review: Path = Path("review")
    output: Path = Path("output")
    extract: Path = Path("extract")
    font: str | None = None              # Chinese font file; None = auto-detect
    notes_pages: str = ""                # pages with long numbered notes, e.g. "7,8"
    tb_anchors: list[str] = field(default_factory=lambda: list(DEFAULT_TB_ANCHORS))
    left_stamp: float = 75.0             # width (pt) of the left margin stamp strip
    names: list[str] = field(default_factory=list)       # never translated (signatures)
    acronyms: list[str] = field(default_factory=list)    # never translated (company codes ...)
    jieba_words: list[str] = field(default_factory=list) # extra Chinese words, never broken
    sections: list[dict] = field(default_factory=list)   # [{name, first, last}] for 05_volume


def _p(root: Path, v) -> Path | None:
    if v in (None, ""):
        return None
    p = Path(v)
    return p if p.is_absolute() else (root / p)


def load(path: str | Path | None = None) -> Config:
    """Load ``path`` (default ./project.toml).  A missing default file gives defaults."""
    explicit = path is not None
    path = Path(path or "project.toml").resolve()
    if not path.exists():
        if explicit:
            raise FileNotFoundError(path)
        c = Config(root=Path.cwd())
        _resolve(c, c.root)
        return c
    d = tomllib.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    p = d.get("project", {})
    tb = d.get("title_block", {})
    keep = d.get("keep", {})
    c = Config(root=root)
    c.name = p.get("name", c.name)
    c.pdf = p.get("pdf")
    c.dxf = p.get("dxf")
    c.tm = p.get("tm", c.tm)
    c.glossaries = p.get("glossaries", [])
    c.review = p.get("review_dir", c.review)
    c.output = p.get("output_dir", c.output)
    c.extract = p.get("extract_dir", c.extract)
    c.font = p.get("font")
    c.notes_pages = str(p.get("notes_pages", ""))
    c.tb_anchors = tb.get("anchors", c.tb_anchors)
    c.left_stamp = float(tb.get("left_stamp_width", c.left_stamp))
    c.names = keep.get("names", [])
    c.acronyms = keep.get("acronyms", [])
    c.jieba_words = d.get("segmentation", {}).get("words", [])
    c.sections = d.get("volume", {}).get("sections", [])
    _resolve(c, root)
    return c


def _resolve(c: Config, root: Path):
    c.pdf = _p(root, c.pdf)
    c.dxf = _p(root, c.dxf)
    c.tm = _p(root, c.tm)
    c.glossaries = [_p(root, g) for g in c.glossaries]
    c.review = _p(root, c.review)
    c.output = _p(root, c.output)
    c.extract = _p(root, c.extract)


def apply(c: Config):
    """Push the keep-lists / segmentation words / font into the engine modules."""
    from . import layout, place
    layout.NAMES.update(c.names)
    layout.ACRONYMS.update(c.acronyms)
    if c.font:
        place.set_font(c.font)
    place.add_words(c.jieba_words)
