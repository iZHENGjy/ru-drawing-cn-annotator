"""QA tooling (scripts/qa): occlusion detector, metrics, baseline comparison, determinism diff.
Synthetic data only: a raster 'scan' with a table rule and text-like ink blocks, plus blue Chinese
spans drawn the way scan/engine.py draws them (colour 002EBF)."""
import json
import subprocess
import sys

import numpy as np
import pymupdf
import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
from qa import common as K, determinism, metrics, occlusion, regress  # noqa: E402

BLUE = (0x00 / 255, 0x2E / 255, 0xBF / 255)
W, H, DPI = 420, 300, 150


def _scan_png():
    s = DPI / 72
    img = np.full((int(H * s), int(W * s)), 255, np.uint8)
    img[int(150 * s):int(150 * s) + 2, int(20 * s):int(400 * s)] = 0                 # table rule at y=150
    for x in range(int(40 * s), int(140 * s), 7):                                    # text-like ink at y 60-70
        img[int(60 * s):int(70 * s), x:x + 3] = 0
    return img


def _make(tmp, spans, front_pages=0, name="out.pdf"):
    """Source scan (image-only) and an output with ``spans`` = [(x, y_baseline, size, text)] in blue."""
    src = pymupdf.open()
    pg = src.new_page(width=W, height=H)
    arr = _scan_png()
    pg.insert_image(pg.rect, pixmap=pymupdf.Pixmap(pymupdf.csGRAY, arr.shape[1], arr.shape[0], arr.tobytes(), False))
    src_path = tmp / "src.pdf"
    src.save(src_path)
    out = pymupdf.open()
    for _ in range(front_pages):
        out.new_page(width=595, height=842).insert_text((50, 50), "目录", fontname="china-s", fontsize=12, color=BLUE)
    out.insert_pdf(pymupdf.open(src_path))
    p = out[-1]
    for x, y, size, text in spans:
        p.insert_text((x, y), text, fontname="china-s", fontsize=size, color=BLUE)
    out_path = tmp / name
    out.save(out_path)
    return src_path, out_path


GOOD = (200, 240, 9, "正常标注")
TINY = (200, 270, 4, "字太小")
CROSS = (200, 153, 12, "压线标注")
INK = (50, 68, 9, "压墨迹")


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("qa")
    return _make(tmp, [GOOD, TINY, CROSS], front_pages=2), tmp


def test_blue_spans_and_offset(synth):
    (src, out), _ = synth
    od, sd = pymupdf.open(out), pymupdf.open(src)
    assert K.page_offset(od, sd) == 2
    assert {t for _, _, t in K.blue_spans(od[2])} == {"正常标注", "字太小", "压线标注"}
    assert len(K.blue_spans(od[0])) == 1           # contents page is blue too, but not a drawing page


def test_detector_flags_exactly_tiny_and_crossing(synth):
    (src, out), _ = synth
    rows, fl = occlusion.run(out, src)
    assert len(rows) == 3
    by = {r["text"]: r["flags"] for r in rows}
    assert by["正常标注"] == []
    assert by["字太小"] == ["tiny"]
    assert by["压线标注"] == ["rule"]
    assert {r["text"] for r in fl} == {"字太小", "压线标注"}
    assert all(r["page"] == 3 and r["src_page"] == 1 for r in fl)


def test_detector_flags_ink_overlap(tmp_path):
    src, out = _make(tmp_path, [GOOD, INK])
    rows, fl = occlusion.run(out, src)
    assert {r["text"]: r["flags"] for r in rows} == {"正常标注": [], "压墨迹": ["ink"]}


def test_detector_zh_overlap(tmp_path):
    src, out = _make(tmp_path, [(200, 240, 9, "重叠一"), (205, 241, 9, "重叠二")])
    _, fl = occlusion.run(out, src)
    assert len(fl) == 2 and all("zh_overlap" in r["flags"] for r in fl)


def test_tiles_merge_and_score(synth):
    (src, out), _ = synth
    rows, fl = occlusion.run(out, src)
    tl = occlusion.tiles(fl)
    assert 1 <= len(tl) <= 2 and all(t["page"] == 3 for t in tl)
    for r in fl:                                   # every flagged box lies inside some tile
        assert any(K.box_overlap(r["bbox"], t["bbox"]) > 0.99 for t in tl)
    todo = [(1, (195, 255, 240, 275), "字过小"), (1, (10, 10, 30, 20), "elsewhere")]
    sc = occlusion.score(fl, todo)
    assert sc["recall"] == 0.5 and sc["flagged_on_finding"] == 1 and sc["precision"] == 0.5


def test_metrics(synth):
    (src, out), tmp = synth
    work = tmp / "work"
    (work / "review").mkdir(parents=True, exist_ok=True)
    (work / "packets").mkdir(exist_ok=True)
    (work / "review" / "build_report.tsv").write_text(
        "T\t1\t引线\tx\nT\t1\t引线\ty\nT\t1\t未放置\tz\nT\t1\t5\tabc\n", encoding="utf-8")
    (work / "packets" / "p01.cands.json").write_text(json.dumps({"cands": {"#1": {}, "C1": {}}}), encoding="utf-8")
    m = metrics.compute(out, work=work, src=src, count_word="件", floor=6.0)
    assert m["pages"] == 3
    assert m["zh_chars"] == 2 * 2 + 4 + 3 + 4
    assert (m["leaders"], m["unplaced"], m["audit_remaining"]) == (2, 1, 2)
    assert m["font_min"] == 4.0 and m["below_floor"] == 1
    assert m["mark_gate"] is None
    assert (m["occ_flagged"], m["occ_tiny"], m["occ_rule"]) == (2, 1, 1)
    m2 = metrics.compute(out, work=work, src=src, count_word="件", floor=6.0)
    assert m["text_sha256"] == m2["text_sha256"] and m["layout_sha256"] == m2["layout_sha256"]
    _, out3 = _make(tmp, [GOOD, TINY, (200, 153, 12, "压线标注X")], front_pages=2, name="out3.pdf")
    m3 = metrics.compute(out3, work=work, count_word="件")
    assert m3["text_sha256"] != m["text_sha256"]
    assert determinism.diff(m, m2) == {}
    assert "text_sha256" in determinism.diff(m, m3)


def test_audit_log_and_counts(tmp_path):
    log = tmp_path / "audit.log"
    log.write_text("p01: x\nremaining 6  pages [7]\n", encoding="utf-8")
    assert metrics.audit_remaining(None, log) == 6
    assert metrics.audit_remaining(None, None) is None


def test_regress_judge_rules():
    r = regress.RULES
    j = lambda k, run, base, tol=None: regress.judge(k, run, base, r[k], tol)  # noqa: E731
    assert j("zh_chars", 20227, 20227) == "equal"
    assert j("zh_chars", 20200, 20227) == "equal"              # inside 0.5 %
    assert j("zh_chars", 19000, 20227) == "worse"
    assert j("zh_chars", 21500, 20227) == "changed"
    assert j("leaders", 198, 197) == "worse" and j("leaders", 190, 197) == "better"
    assert j("unplaced", 1, 0) == "worse" and j("unplaced", 0, 0) == "equal"
    assert j("audit_remaining", 6, 6) == "equal" and j("audit_remaining", 7, 6) == "worse"
    assert j("font_min", 3.0, 3.17) == "worse" and j("font_min", 3.17, 3.17) == "equal"
    assert j("mark_gate", 1, 0) == "worse"
    assert j("pages", 47, 46) == "worse"
    assert j("text_sha256", "a", "b") == "changed" and j("text_sha256", "a", "a") == "equal"
    assert j("leaders", None, 5) == "skipped"
    assert j("leaders", 199, 197, tol=5) == "equal"


def test_regress_compare_and_cli(tmp_path):
    base = dict(pages=3, zh_chars=1000, leaders=5, unplaced=0, audit_remaining=0, font_min=4.5, text_sha256="a")
    same = dict(base)
    rows, failed = regress.compare(same, base)
    assert not failed and all(v == "equal" for _, _, _, v in rows)
    better = dict(base, leaders=3, font_min=5.0)
    rows, failed = regress.compare(better, base)
    assert not failed and {k: v for k, _, _, v in rows}["leaders"] == "better"
    worse = dict(base, unplaced=2)
    assert regress.compare(worse, base)[1]
    fp = dict(base, text_sha256="b")
    assert not regress.compare(fp, base)[1] and regress.compare(fp, base, strict=True)[1]
    for name, d in (("base", base), ("ok", better), ("bad", worse)):
        (tmp_path / f"{name}.json").write_text(json.dumps(d), encoding="utf-8")
    script = str(ROOT / "scripts" / "qa" / "regress.py")
    ok = subprocess.run([sys.executable, script, str(tmp_path / "ok.json"), "--baseline", str(tmp_path / "base.json")],
                        capture_output=True, text=True)
    bad = subprocess.run([sys.executable, script, str(tmp_path / "bad.json"), "--baseline", str(tmp_path / "base.json")],
                         capture_output=True, text=True)
    assert ok.returncode == 0 and "OK" in ok.stdout
    assert bad.returncode == 1 and "REGRESSION" in bad.stdout


def test_metrics_cli(synth, tmp_path):
    (src, out), _ = synth
    js = tmp_path / "m.json"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "qa" / "metrics.py"), str(out), "--src", str(src),
                        "--json", str(js)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    m = json.loads(js.read_text(encoding="utf-8"))
    assert m["pages"] == 3 and m["occ_flagged"] == 2
