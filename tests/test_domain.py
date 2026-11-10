"""Domain features (synthetic data only): reference appendix, compact output, low-confidence marker,
and that the default build is unchanged when none of them is requested."""
import hashlib
import json
import re

import pymupdf
import pytest
from conftest import ROOT
from test_scan import load_step, proj  # noqa: F401  (proj: synthetic scan project fixture)

from scan import appendix, engine


def ids(hits):
    return {h["row"]["id"] for h in hits}


def build(proj, name="d.pdf"):
    out = proj["tmp"] / "out" / name
    load_step("20_build.py").build(proj["ctx"], [1], out, verbose=False)
    d = pymupdf.open(out)
    return d[0].read_contents(), d[0].get_text()


@pytest.fixture(autouse=True)
def _reset_opts():
    engine.OPTS["compact"] = False
    yield
    engine.OPTS["compact"] = False


# ------------------------------------------------------------------ appendix
def test_data_files_wellformed():
    rows = appendix.load_refs()
    assert len(rows) >= 50
    seen = set()
    for r in rows:
        assert r["id"] not in seen; seen.add(r["id"])
        assert r["level"] in ("近似", "无直接对应") and r["note"] and r["cn"]
        re.compile(r["pat"], re.I)
        if r["level"] == "无直接对应":
            assert r["cn"] == "—"            # never an invented counterpart
    for fn in ("ref_materials.json", "ref_standards.json"):
        assert json.loads((ROOT / "glossary" / fn).read_text(encoding="utf-8"))["disclaimer"] == appendix.DISCLAIMER


def test_appendix_selects_rows_by_detected_grades():
    texts = {1: ["Арматура класса А500С, бетон В25, W8, F200", "сталь С345 по ГОСТ 27772-2015", "сварка по ГОСТ 5264-80"],
             3: ["钢筋 А500С Ø16,混凝土 B25,抗渗 W8;СП 63.13330.2018", "В500С", "Фм1.1 В250 А-III"]}
    h = appendix.detect(texts)
    got = ids(h)
    assert {"rb_a500", "c_b25", "c_w", "c_f", "s_345", "gost27772", "gost5264", "sp63", "rb_b500", "rb_a400"} <= got
    assert not got & {"rb_a240", "c_b20", "c_b30", "s_235", "s_390", "gost14098", "sp16", "c_b50"}   # В250 is not В25/В50
    a500 = next(x for x in h if x["row"]["id"] == "rb_a500")
    assert a500["pages"] == [1, 3] and a500["tokens"]["А500С"] == 2
    assert next(x for x in h if x["row"]["id"] == "rb_b500")["row"]["level"] == "无直接对应"
    assert appendix.detect({1: ["nothing relevant here Фм1.1 Км2.1"]}) == []


def test_appendix_pages_all_carry_disclaimer_and_bookmark(proj, tmp_path):
    texts = {1: [f"бетон В{n}" for n in (15, 20, 25, 30, 35, 40, 45, 50, 55, 60)]
             + ["А240 А400 А500С В500С А600", "С235 С245 С345 С390 С440 09Г2С Ст3сп", "W8 F150",
                "ГОСТ 5264 ГОСТ 14771 ГОСТ 8713 ГОСТ 14098 ГОСТ 34028 ГОСТ 10060 ГОСТ 8239 ГОСТ 9467 ГОСТ 9.307",
                "СП 63.13330 СП 16.13330 СП 20.13330 СП 14.13330 СП 22.13330 СП 28.13330 СП 70.13330 ГОСТ Р 21.101"]}
    hits = appendix.detect(texts)
    data, n = appendix.render(hits, "X")
    assert n >= 2                                       # long enough to need a continuation page
    d = pymupdf.open("pdf", data)
    assert d.page_count == n
    for pg in d:
        assert appendix.DISCLAIMER in pg.get_text().replace("\n", "")
    allt = "".join(p.get_text() for p in d).replace("\n", "")
    assert "HRB500" in allt and "无直接对应" in allt and "Q355" in allt
    base = tmp_path / "base.pdf"
    pymupdf.open(proj["ctx"].cfg.pdf).save(base)
    out = tmp_path / "with.pdf"
    assert appendix.append_to_pdf(base, out, hits, "X") == n
    f = pymupdf.open(out)
    assert f.page_count == 1 + n and f.get_toc()[-1][2] == 2 and "附录" in f.get_toc()[-1][1]
    assert appendix.append_to_pdf(base, tmp_path / "none.pdf", [], "X") == 0
    assert pymupdf.open(tmp_path / "none.pdf").page_count == 1


def test_collect_texts_reads_notes_and_map(proj):
    ctx = proj["ctx"]
    mp = json.loads(ctx.map(1).read_text(encoding="utf-8"))
    mp["extra"] = [{"zh": "混凝土 В30", "box": [0.1, 0.1, 0.2, 0.2]}]
    ctx.map(1).write_text(json.dumps(mp, ensure_ascii=False), encoding="utf-8")
    nt = json.loads(ctx.notes(1).read_text(encoding="utf-8"))
    nt["notes"] = [{"no": "1", "ru": "Арматура А400", "zh": "钢筋 HRB400"}]
    ctx.notes(1).write_text(json.dumps(nt, ensure_ascii=False), encoding="utf-8")
    got = ids(appendix.detect(appendix.collect_texts(ctx, [1])))
    assert {"c_b30", "rb_a400"} <= got


# ------------------------------------------------------------------ compact
def test_compact_removes_count_labels_only(proj):
    _, t0 = build(proj, "a.pdf")
    assert "根" in t0 and "шт./pcs.=根数" in t0.replace("\n", "")
    engine.OPTS["compact"] = True
    _, t1 = build(proj, "b.pdf")
    assert "根" not in t1 and "шт./pcs." not in t1.replace("\n", "").replace("шт./pcs.4", "")
    for d in proj["ru"]:
        if d is not proj["blank"]:
            assert f"译{d['id']}" in t1                       # everything else still there


# ------------------------------------------------------------------ low confidence
DASH = re.compile(rb"\[ ?0\.5 1\.1 ?\] 0 d")


def meta(proj, lines=None, extra=None):
    fn = proj["ctx"].f("map", 1, ".meta.json")
    if lines is None:
        fn.unlink(missing_ok=True)
        return
    fn.write_text(json.dumps({"lines": lines, "extra": extra or []}), encoding="utf-8")


def test_low_confidence_marker_only_for_low_entries(proj):
    ru = [d for d in proj["ru"] if d is not proj["blank"]]
    meta(proj)
    c0, t0 = build(proj, "n.pdf")
    assert not DASH.search(c0) and "虚线下划线" not in t0
    meta(proj, {str(d["id"]): {"conf": "high", "src": "tm"} for d in ru})
    c1, t1 = build(proj, "h.pdf")
    assert c1 == c0 and t1 == t0                              # all-high sidecar == no sidecar
    half = ru[: len(ru) // 2]
    meta(proj, {str(d["id"]): {"conf": "low", "src": "llm"} for d in half} | {str(d["id"]): {"conf": "high"} for d in ru[len(ru) // 2:]})
    c2, t2 = build(proj, "l.pdf")
    n_half = len(DASH.findall(c2))
    assert n_half >= 1 and "虚线下划线" in t2.replace(chr(10), "")
    meta(proj, {str(d["id"]): {"conf": "low"} for d in ru})
    c3, t3 = build(proj, "l2.pdf")
    assert len(DASH.findall(c3)) > n_half                     # more low entries -> more marks
    assert all(f"译{d['id']}" in t3 for d in ru)              # the Chinese itself is unchanged
    meta(proj, {"999": {"conf": "low"}}, extra=[{"conf": "high"}, {"conf": "low"}])   # low flag for ids / extras that are not drawn
    c4, t4 = build(proj, "l3.pdf")
    assert not DASH.search(c4) and "虚线下划线" not in t4
    meta(proj)


# ------------------------------------------------------------------ default unchanged
def test_default_output_is_deterministic_and_flag_free(proj):
    meta(proj)
    engine.OPTS["compact"] = False
    a, ta = build(proj, "x1.pdf"); b, tb = build(proj, "x2.pdf")
    assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest() and ta == tb
    assert not DASH.search(a)
