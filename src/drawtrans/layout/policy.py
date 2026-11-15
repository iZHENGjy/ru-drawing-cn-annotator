"""Layout policy: every number of the output standard in one place (docs/output_standard.md §8).

The engine (``layout.core``), the scan placer (``layout.scan_place``, ``output_standard = 3``) and the QA
gate (``qa.standard``, ``qa.occlusion``) read the same object, so "the engine placed it, QA flags it"
cannot happen by construction; ``tests/test_layout_core.py::test_policy_matches_qa`` keeps it that way.

Sizes are **printed** sizes: a sheet is printed on A3 (drawings) or A4 (documents), so a label on a
3703 pt wide sheet must be ``6 / 0.32 = 18.7 pt`` in the PDF to print at 6 pt.  ``Policy.scale(w, h)``
gives the print factor of a page, ``Policy.pt(print_pt, scale)`` the PDF size.
"""
from __future__ import annotations

from dataclasses import dataclass

A4_LONG = 841.89          # pt
A3_LONG = 1190.55
A1_LONG = 2383.94


@dataclass(frozen=True)
class Policy:
    standard: int = 3
    # ---- type size (printed pt)
    label_floor: float = 6.0          # no label prints smaller (hard)
    label_pref_min: float = 7.0       # preferred minimum: p10 >= 7 pt
    small_share_max: float = 0.05     # share of labels below label_pref_min
    label_ratio: float = 0.95         # translation = 0.95 x source size
    shrink_steps: tuple = (1.0, 0.93, 0.86, 0.8)   # never below 0.8 x preferred; then wrap, then move
    column_pref: float = 9.0          # extension column (printed)
    column_floor: float = 6.5
    legend_floor: float = 6.5         # title-block legend line
    legend_pref: float = 7.5
    legend_pitch: float = 1.2         # line spacing of the legend
    stamp_floor: float = 7.0          # approval-stamp translation, one line outside the stamp
    line_pitch: float = 1.18          # label line spacing (renderer baseline 0.92 fs)
    max_label_lines: int = 3
    # ---- clearances (PDF pt unless noted)
    gap_below: float = 0.15           # x fs between source and translation
    gap_placed: float = 1.2           # between two translations
    rule_clear_min: float = 0.5       # clearance to any rule: max(0.5 pt, 0.10 fs)
    rule_clear_k: float = 0.10
    page_margin: float = 6.0
    # ---- occlusion (engine filter = QA flag)
    ink_box: float = 0.03             # dark source ink inside the translation box (share)
    ink_glyph: float = 0.15           # dark source ink under the glyph envelope (QA)
    raster_dpi: int = 150             # obstacle raster
    rule_run_px: int = 8              # a rule: >= 8 px dark run at 150 dpi (3.8 pt) ...
    rule_gap_px: int = 3              # ... dashed rules: gaps <= 3 px are closed first
    # ---- leaders
    leader_share_max: float = 0.03
    leader_mean_max: float = 15.0     # pt
    leader_max_small: float = 30.0    # pages up to A2
    leader_max_large: float = 60.0    # A1 and larger
    leaders_per_page: int = 5
    leader_ink_max: float = 0.10      # ink share along a leader that counts as "crossing source text"
    # ---- candidates
    max_candidates: int = 5           # scored per tier
    shift_lines: int = 4              # "below, shifted down" up to this many source lines

    # ------------------------------------------------------------------ print scale
    @staticmethod
    def scale(width: float, height: float) -> float:
        """print factor of a page: documents (long side <= 900 pt) go on A4, drawings on A3, in the
        orientation of the page, fitting both sides (a page widened by the column prints smaller); never enlarged."""
        long, short = max(width, height), min(width, height)
        t_long, t_short = (A4_LONG, A4_LONG / 2 ** 0.5) if long <= 900 else (A3_LONG, A3_LONG / 2 ** 0.5)
        return min(1.0, t_long / long, t_short / short)

    @staticmethod
    def pt(print_pt: float, scale: float) -> float:
        """PDF size that prints at ``print_pt``."""
        return print_pt / max(scale, 1e-6)

    def leader_max(self, width: float, height: float) -> float:
        return self.leader_max_large if max(width, height) >= A1_LONG * 0.95 else self.leader_max_small

    def rule_clear(self, fs: float) -> float:
        return max(self.rule_clear_min, self.rule_clear_k * fs)


V2 = Policy()

# The legacy (v1 / v2-standard) placer's numbers, for reference and for the QA of old deliveries.
LEGACY = {"label_floor_small": 4.5, "label_floor_big": 6.0, "label_floor_std1_small": 3.2, "cell_floor": 4.0,
          "ink_box_engine": 0.07, "rule_inset": 0.22, "raster_dpi": 72, "rule_run_px": 18, "placed_pad": 0.8}


def for_standard(std: int) -> Policy | None:
    """the policy object of an output standard (None: the legacy placer, frozen for byte-identical rebuilds)."""
    return V2 if int(std or 1) >= 3 else None
