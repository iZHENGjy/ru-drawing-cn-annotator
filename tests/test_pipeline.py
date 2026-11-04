"""End-to-end on the synthetic demo: placement must never cover original text or other
Chinese, keep-mode must leave the original untouched, replace-mode must remove only English."""
import importlib.util
from collections import Counter

import ezdxf
import pymupdf
import pytest
from conftest import ROOT
from engine import config, layout
from engine.pdf_build import build
from engine.tm import TM

DEMO = ROOT / "demo"


@pytest.fixture(scope="module")
def cfg():
    c = config.load(DEMO / "project.toml")
    config.apply(c)
    return c


def run(cfg, tmp_path, pages, english):
    out = tmp_path / f"out_{english}.pdf"
    rows = build(cfg.pdf, pages, str(out), TM(cfg.tm, cfg.glossaries), tmp_path / "review",
                 english=english, tb_anchors=cfg.tb_anchors, left_stamp=cfg.left_stamp, verbose=False)
    return rows, pymupdf.open(out)


def original_words(page):
    return Counter(w.text for w in layout.page_words(page))


@pytest.mark.parametrize("english", ["keep", "replace"])
def test_demo_qa_is_clean(cfg, tmp_path, english):
    rows, _ = run(cfg, tmp_path, [1, 2], english)
    for r in rows:
        assert r["missing"] == 0 and r["unplaced"] == 0, r["issues"]
        assert r["overlap"] == 0, r["issues"]               # no Chinese over text / Chinese / page edge
        assert r["ru_lost"] == 0 and r["en_residual"] == 0, r["issues"]


def drawn_rects(out_page, src_page):
    """rects of the words that exist only in the output (= the Chinese annotations)"""
    before = original_words(src_page)
    new = []
    for w in layout.page_words(out_page):
        if before[w.text] > 0:
            before[w.text] -= 1
        else:
            new.append(w)
    return new


def test_chinese_never_overlaps_original_text(cfg, tmp_path):
    src = pymupdf.open(cfg.pdf)
    _, out = run(cfg, tmp_path, [1], "keep")
    added = drawn_rects(out[0], src[0])
    assert added and any("混凝土" in w.text for w in added)
    orig = layout.page_words(src[0])
    for a in added:
        for o in orig:
            inter = a.rect & o.rect
            assert inter.is_empty or inter.get_area() < 0.25 * min(a.rect.get_area(), o.rect.get_area()), (a.text, o.text)


def test_keep_mode_leaves_original_intact(cfg, tmp_path):
    src = pymupdf.open(cfg.pdf)
    _, out = run(cfg, tmp_path, [1, 2], "keep")
    for i in range(2):
        before, after = original_words(src[i]), original_words(out[i])
        assert not (before - after), "original text removed"
        assert len(out[i].get_drawings()) == len(src[i].get_drawings()), "line art changed"
    assert "General" in out[1].get_text()


def test_replace_mode_removes_english_only(cfg, tmp_path):
    src = pymupdf.open(cfg.pdf)
    _, out = run(cfg, tmp_path, [2], "replace")
    before, after = original_words(src[1]), original_words(out[0])
    lost = before - after
    assert lost and all(layout.word_lang(w) in ("en", "neu") for w in lost)
    assert "General" not in out[0].get_text() and "Общие" in out[0].get_text()
    assert "总说明" in out[0].get_text()


def test_dxf_overlay(cfg, tmp_path):
    src = tmp_path / "plan.dxf"
    spec = importlib.util.spec_from_file_location("make_dxf", DEMO / "make_demo_dxf.py")
    import sys
    argv, sys.argv = sys.argv, ["make_demo_dxf.py", str(src)]
    try:
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
    finally:
        sys.argv = argv
    (tmp_path / "project.toml").write_text(
        f'[project]\ntm = "{cfg.tm.as_posix()}"\nglossaries = ["{cfg.glossaries[0].as_posix()}"]\n'
        f'review_dir = "review"\noutput_dir = "out"\n', encoding="utf-8")
    spec = importlib.util.spec_from_file_location("cad_build", ROOT / "scripts" / "cad" / "10_build.py")
    cad = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cad)
    cad.main(["--config", str(tmp_path / "project.toml"), "--dxf", str(src)])
    full = ezdxf.readfile(tmp_path / "out" / "plan_CN.dxf")
    overlay = ezdxf.readfile(tmp_path / "out" / "plan_CN_overlay.dxf")
    cn = [e.dxf.text for e in overlay.modelspace().query("TEXT") if e.dxf.layer == cad.LAYER]
    assert "机器间" in cn and cn.count("水泵") == 2            # text inside a block, inserted twice
    orig = ezdxf.readfile(src)
    n_orig = len(orig.modelspace())
    assert len(full.modelspace()) == n_orig + len(cn)         # originals untouched, Chinese added
