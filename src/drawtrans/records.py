"""Data passed between the modules, defined in one place.

The pipeline exchanges plain JSON-able records (they are also the files in the work folder, so a run
can be resumed and inspected).  These TypedDicts name their fields; each one says which module writes
it and which modules read it.

    OCR / text layer ──► OcrLine ──► segment ──► Segment ──► memory (TM) ──► PageMap ──► layout ──► Placement
                                                     ▲                          ▲
                                         llm: PhraseAnswer               llm: PageNotes
"""
from __future__ import annotations

from typing import Literal, TypedDict

Box = list[float]            # [x0, y0, x1, y1]; page fractions in map files, PDF points in placements


class OcrLine(TypedDict):
    """One numbered OCR line of a page (work/lines/pNN.tsv; ocr.tesseract writes, segment/memory/layout read)."""
    id: int
    text: str
    box: Box                 # page fractions


class Segment(TypedDict, total=False):
    """A unique phrase of the volume (work/seg/segments.json; segment.segments writes, memory.store reads)."""
    id: str
    key: str                 # normalised text, numbers as ⟨1⟩ ⟨2⟩ …
    fold: str                # equality form (case, look-alikes, spacing folded)
    sig: str                 # number signature
    n: int                   # occurrences
    need: bool               # needs a translation decision
    sample: str              # one raw reading
    ctx: list[str]           # line above / below


class TMEntry(TypedDict, total=False):
    """A translation-memory value (tm/project.json; memory.store)."""
    key: str
    zh: str                  # target-language text (the name is historical: any target language)
    action: Literal["translate", "keep", "skip"]
    conf: Literal["high", "low"]
    src: str                 # llm | tm | human | rule | glossary


class Extra(TypedDict, total=False):
    """A translation placed at a box that is not one OCR line (audit fix, joined label, automap)."""
    box: Box
    zh: str
    ru: str                  # source text (historical name: any source language)
    src: str


class Paragraph(TypedDict, total=False):
    """A running-text paragraph translated whole (memory.automap -> layout.scan_place extension column)."""
    ids: list[int]
    box: Box
    ru: str
    zh: str
    kind: str                # body | tb
    low: bool


class PageMap(TypedDict, total=False):
    """work/map/pNN.json (+ .fix.json): line id -> translation; memory.automap and qa.apply_audit write it."""
    lines: dict[str, str]
    extra: list[Extra]
    paras: list[Paragraph]
    accept: list[dict]
    appendix: bool


class NoteClause(TypedDict):
    no: str
    ru: str
    zh: str


class PageNotes(TypedDict, total=False):
    """Answer of the LLM task page_notes (work/job/notes_results.json)."""
    sheet_code: str
    format: str
    title_ru: str
    title_zh: str
    notes: list[NoteClause]
    labels: list[dict]
    issues: list[str]
    notes_from: int          # the notes were reused from this sheet ([job] optimize)


class Placement(TypedDict, total=False):
    """One placed target-language text (``*.placements.json``; layout writes via render.placelog,
    qa.standard reads)."""
    route: Literal["scan", "text"]
    pg: int
    kind: Literal["label", "block", "column", "para", "row"]
    text: str
    ru: str
    fs: float                # font size, pt
    bb: Box                  # placed box, pt
    src: Box                 # source text box, pt
    y: float
    vertical: bool
    leader: bool
    tb: bool                 # a title-block field drawn in its cell
    std: int                 # output standard the page was made under (1 legacy, 2 docs/output_standard.md)


class StandardResult(TypedDict, total=False):
    """qa.standard.check() result (work/job/standard.json)."""
    ok: bool
    violations: list[dict]
    advisory: list[dict]
    advisory_n: int
    counts: dict[str, int]
    missing: str
