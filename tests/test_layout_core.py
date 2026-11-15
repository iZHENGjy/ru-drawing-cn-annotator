"""Output standard v2 placement engine: one test per rule on synthetic scenes (no PDF)."""
import numpy as np
import pytest

from drawtrans.layout import core
from drawtrans.layout.policy import V2, Policy

DPI = 150
K = DPI / 72


def scene(w=400, h=300, hlines=(), vlines=(), dashed=(), ink=()):
    """grayscale page: hlines (x0, x1, y), vlines (x, y0, y1), dashed hlines, ink boxes (pt)."""
    g = np.full((int(h * K), int(w * K)), 255, np.uint8)
    for x0, x1, y in hlines:
        g[int(y * K), int(x0 * K):int(x1 * K)] = 0
    for x, y0, y1 in vlines:
        g[int(y0 * K):int(y1 * K), int(x * K)] = 0
    for x0, x1, y in dashed:                       # 6 px dash, 2 px gap
        for px in range(int(x0 * K), int(x1 * K), 8):
            g[int(y * K), px:px + 6] = 0
    for x0, y0, x1, y1 in ink:
        g[int(y0 * K):int(y1 * K), int(x0 * K):int(x1 * K)] = 0
    return g


def engine(g, w=400, h=300, boxes=(), scale=1.0, policy=V2):
    board = core.RasterBoard(g, w, h, policy, boxes=boxes)
    return core.Engine(board, core.Metrics(None), policy, scale)


SRC = (50, 100, 120, 110)


def src(zh="基础混凝土", rect=SRC, size=10, **kw):
    return core.Source("s", zh, rect, size, own=(rect,), **kw)


def test_r1_below_left_aligned():
    e = engine(scene(), boxes=[SRC])
    p = e.place_one(src())
    assert p.status == "placed" and p.cand.mode == "below" and p.cand.tier == 0
    assert p.cand.rect[0] == SRC[0] and p.cand.rect[1] > SRC[3]


def test_r2_never_above_the_source():
    blockers = [(40, 111, 300, 160), (121, 90, 300, 112), (0, 90, 49, 112)]   # below, right, left taken
    e = engine(scene(), boxes=[SRC, *blockers])
    p = e.place_one(src())
    if p.status == "placed":
        assert p.cand.rect[1] >= SRC[1] or p.cand.rect[0] >= SRC[2] or p.cand.rect[2] <= SRC[0]
        assert not (p.cand.rect[3] <= SRC[1] + 0.5 and p.cand.rect[0] < SRC[2] and SRC[0] < p.cand.rect[2])


def test_r4_clearance_to_rules_no_inset():
    # a rule 3 pt under the source: "below" would touch it, the label must keep rule_clear
    g = scene(hlines=[(0, 400, 113)])
    e = engine(g, boxes=[SRC])
    p = e.place_one(src())
    assert p.status == "placed"
    c = V2.rule_clear(p.cand.fs)
    r = p.cand.rect
    assert e.b.rule_px((r[0] - c, r[1] - c, r[2] + c, r[3] + c)) == 0


def test_r5_move_before_shrink():
    # below is too narrow for the preferred size (a rule closes it), the right side is free
    g = scene(hlines=[(0, 400, 116)])
    e = engine(g, boxes=[SRC])
    p = e.place_one(src())
    assert p.cand.fs == pytest.approx(e.sizes(src())[0])          # preferred size kept
    assert p.cand.mode in ("right", "below_shift")


def test_r6_floor_never_crossed_column_instead():
    # everything around is ink: no candidate; the label goes to the column, not to a tiny size
    g = scene(ink=[(0, 0, 400, 300)])
    e = engine(g, boxes=[SRC])
    p = e.place_one(src())
    assert p.status == "column"
    floor = V2.pt(V2.label_floor, 1.0)
    assert all(f >= floor - 1e-6 for f in e.sizes(src()))
    assert min(e.sizes(src())) >= 0.8 * e.sizes(src())[0] - 1e-6


def test_r7_print_scale_raises_the_size():
    e = engine(scene(w=3703, h=1753), w=3703, h=1753, boxes=[SRC], scale=Policy.scale(3703, 1753))
    assert e.sizes(src())[0] >= 7.0 / Policy.scale(3703, 1753) - 1e-6        # prints at >= 7 pt


def test_r8_dashed_rules_are_rules():
    g = scene(dashed=[(0, 400, 200)])
    b = core.RasterBoard(g, 400, 300, V2)
    assert b.rule_px((100, 198, 140, 202)) > 0


def test_r9_leader_does_not_cross_text():
    # source boxed in by text on three sides and below; a free block lies 20 pt to the right
    around = [(40, 111, 300, 125), (121, 95, 140, 112), (0, 95, 49, 112), (40, 125, 300, 300)]
    e = engine(scene(), boxes=[SRC, *around])
    p = e.place_one(src())
    if p.status == "placed" and p.cand.leader:
        (x0, y0), (x1, y1) = p.cand.leader
        for t in np.linspace(0.05, 0.95, 40):
            x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            assert not any(b[0] < x < b[2] and b[1] < y < b[3] for b in around)


def test_r10_leader_end_points_on_the_edges():
    pa, pb = core._nearest((0, 0, 10, 10), (30, 2, 40, 8))
    assert pa[0] == 10 and pb[0] == 30 and 2 <= pa[1] <= 8


def test_r16_deterministic():
    def run():
        e = engine(scene(hlines=[(0, 400, 140)]), boxes=[SRC])
        return [(p.status, p.cand and p.cand.rect) for p in (e.place_one(src()), e.place_one(src("钢筋", (200, 50, 240, 60))))]
    assert run() == run()


def test_marker():
    assert core.marker(1) == "①" and core.marker(10) == "⑩" and core.marker(11) == "(11)"


def test_policy_matches_qa():
    """engine and QA read the same numbers."""
    from drawtrans.qa import occlusion, standard
    assert standard.policy_floor(3, 1.0) == V2.label_floor
    assert standard.LEADER_MAX_SMALL == V2.leader_max_small and standard.LEADERS_PER_PAGE == V2.leaders_per_page
    assert occlusion.V2_THRESHOLDS["ink_box"] == V2.ink_box and occlusion.V2_THRESHOLDS["ink_glyph"] == V2.ink_glyph
    assert occlusion.V2_THRESHOLDS["dpi"] == V2.raster_dpi


def test_visual_review_sample_covers_every_page():
    from drawtrans.pipeline.stages_scan import sample_tiles
    tiles = [{"src_page": 1, "flags": ["tiny"], "n": 1}] * 50 + [{"src_page": 2, "flags": ["rule"], "n": 1},
                                                                 {"src_page": 3, "flags": ["ink"], "n": 1}]
    got = sample_tiles(tiles, 6)
    assert {t["src_page"] for t in got} == {1, 2, 3} and len(got) == 6
