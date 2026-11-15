"""Language pairs, output-standard gate, samples, quality and cost helpers (synthetic data only)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from drawtrans import languages as i18n  # noqa: E402


@pytest.fixture(autouse=True)
def _ru_zh():
    i18n.activate("ru", "zh")
    yield
    i18n.activate("ru", "zh")


# ------------------------------------------------------------------ i18n
def test_default_pair_keeps_patterns():
    assert i18n.pair().default
    assert i18n.adapt(r"[A-Za-zА-Яа-яЁё]{2,}") == r"[A-Za-zА-Яа-яЁё]{2,}"
    assert i18n.SRC_CHAR.pattern == "[А-Яа-яЁё]"
    assert i18n.TGT_CHAR.pattern == "[一-鿿]"
    assert i18n.pair().tess_lang == "rus+eng" and i18n.pair().rapid_lang == "ESLAV"


def test_adapt_and_activate_from_config():
    i18n.from_config({"project": {"source_lang": "en", "target_lang": "zh"}})
    assert i18n.adapt(r"[а-яё]{3,}") == "[a-z]{3,}"
    assert i18n.SRC_RUN.findall("Steel 钢 Арматура") == ["Steel"]
    assert not i18n.pair().lookalike and i18n.pair().jieba
    i18n.activate("zh", "en")
    assert i18n.SRC_RUN.findall("钢筋 rebar") == ["钢筋"]
    assert i18n.has_target("Rebar") and not i18n.has_target("钢筋")
    assert i18n.untranslated("钢") and "{SRC}" not in i18n.pair().prompt_vars()["SRC"]
    with pytest.raises(ValueError):
        i18n.activate("xx", "zh")


def test_re_proxy_and_lazy_regex_follow_the_pair():
    from drawtrans.languages import re_src as re
    word = re.compile(r"[А-Яа-яЁё]{3,}")
    assert word.findall("Арматура steel") == ["Арматура"]
    i18n.activate("en", "zh")
    assert word.findall("Арматура steel") == ["steel"]
    assert re.sub(r"[а-я]+", "_", "ab Жк") == "_ Жк"


def test_word_lang_generic():
    from drawtrans.layout.analyse import word_lang
    assert [word_lang(w) for w in ("Арматура", "A500C", "Steel")] == ["ru", "neu", "en"]
    i18n.activate("en", "zh")
    assert [word_lang(w) for w in ("Reinforcement", "HRB400", "mm", "B1")] == ["ru", "neu", "neu", "neu"]
    i18n.activate("zh", "en")
    assert [word_lang(w) for w in ("钢筋", "Steel")] == ["ru", "neu"]


def test_validator_by_pair():
    from drawtrans.llm import validate as V
    assert V.check_against("钢筋 арматура", "арматура")[0].startswith("untranslated Russian word")
    i18n.activate("en", "zh")
    assert any("untranslated English word 'steel'" in e for e in V.check_against("钢 steel", "steel plate"))
    assert V.check_against("钢板 PL-30", "BASE PLATE PL-30") == []
    i18n.activate("zh", "en")
    assert any("untranslated" in e for e in V.check_against("Plate 钢板", "钢板"))
    assert V.has_cjk("Steel plate")          # "target text present" for an English target


def test_prompts_by_pair():
    from drawtrans.llm import tasks as T
    t = T.get("phrase_translate")
    ru = t.prompt
    assert ru.startswith("You translate short Russian texts")
    i18n.activate("en", "zh")
    en = t.prompt
    assert "English" in en and "Simplified Chinese" in en and "{SRC}" not in en
    i18n.activate("ru", "zh")
    assert t.prompt == ru


def test_glossary_layout_and_legacy_paths(tmp_path):
    from drawtrans.memory import store as tmstore
    assert all(p.exists() for p in tmstore.DEFAULT_GLOSSARIES)
    assert tmstore.legacy(ROOT / "glossary" / "gost_general.json") == ROOT / "glossary" / "ru-zh" / "gost_general.json"
    i18n.activate("en", "zh")
    gl = tmstore.base_glossaries()
    assert gl and all(p.exists() and p.parent.name == "en-zh" for p in gl)
    tm = tmstore.TMStore({"project": str(tmp_path / "tm.json")}, root=tmp_path)
    assert tm.lookup("Checked").zh == "校核"


def test_sheet_sense():
    from drawtrans.llm.validate import sheet_sense
    from drawtrans.memory.store import SHEET_HDR
    assert sheet_sense("Лист / Sheet", "钢板") and sheet_sense("Лист", "页") is None
    assert sheet_sense("Лист ⟨1⟩x⟨2⟩", "钢板 ⟨1⟩x⟨2⟩") is None and not SHEET_HDR.fullmatch("Лист ⟨1⟩")


# ------------------------------------------------------------------ output-standard gate
def test_standard_gate_rules():
    from drawtrans.qa import standard as QS
    ok = [dict(kind="label", pg=1, bb=[10, 30, 50, 36], src=[10, 20, 50, 28], fs=6, std=2),
          dict(kind="column", pg=1, text="1. 说明", fs=9, std=2)]
    assert QS.check(ok)["ok"]
    bad = [dict(kind="label", pg=1, bb=[10, 10, 50, 18], src=[10, 20, 50, 28], fs=6, std=2, text="上"),
           dict(kind="label", pg=1, bb=[400, 500, 440, 506], src=[400, 490, 440, 498], fs=5, std=2, tb=True),
           dict(kind="para", pg=2, ru="Бетон укладывать слоями", text="a", std=2),
           dict(kind="para", pg=2, ru="толщиной не более 300 мм.", text="b", std=2),
           dict(kind="row", pg=3, y=10, text="序号 | 名称", std=2), dict(kind="row", pg=3, y=20, text="1. 钢板", std=2),
           dict(kind="row", pg=3, y=30, text="2. 螺栓", std=2), dict(kind="row", pg=3, y=40, text="垫圈", std=2),
           dict(kind="column", pg=3, text="小字", fs=5.0, std=2)]
    r = QS.check(bad)
    assert not r["ok"]
    assert {k: v for k, v in r["counts"].items() if v} == {"above": 1, "split_paragraph": 1, "title_block_cell": 1,
                                                          "row_number": 1, "font_floor": 1}
    legacy = [dict(bad[0], std=1)]
    assert QS.check(legacy)["ok"] and QS.check(legacy)["advisory_n"] == 1


# ------------------------------------------------------------------ samples, label groups, consistency, cost helpers
def _pdf(path, kind):
    import pymupdf
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    if kind == "prose":
        for i in range(40):
            p.insert_text((50, 60 + i * 18), "Concrete shall be placed in layers not thicker than " * 2, fontname="helv", fontsize=9)
    elif kind == "table":
        for i in range(12):
            p.draw_line((40, 80 + i * 40), (555, 80 + i * 40), width=1.5)
        for x in (40, 150, 300, 450, 555):
            p.draw_line((x, 80), (x, 520), width=1.5)
    else:
        p.draw_rect(pymupdf.Rect(100, 100, 400, 400), width=2)
        p.draw_circle((250, 250), 60)
    doc.save(path)


def test_samples_classifier(tmp_path):
    import pymupdf

    from drawtrans.pipeline import samples as SM
    kinds = {}
    for k in ("prose", "table", "drawing"):
        f = tmp_path / f"{k}.pdf"; _pdf(f, k)
        pg = pymupdf.open(f)[0]
        kinds[k] = SM.classify(SM.page_features(pg, len(pg.get_text()) > 25))
    assert kinds == {"prose": "prose", "table": "table", "drawing": "drawing"}
    f = tmp_path / "prose.pdf"
    pg = pymupdf.open(f)[0]
    assert SM.classify(SM.page_features(pg, True), scanned=True) == "letter"


def test_label_groups():
    from drawtrans.segment.segments import label_groups
    L = [dict(id=1, box=[0.10, 0.100, 0.30, 0.110], text="Бетон класса B20"),
         dict(id=2, box=[0.10, 0.112, 0.28, 0.122], text="для создания уклона"),
         dict(id=3, box=[0.50, 0.300, 0.70, 0.310], text="Узел 1."),
         dict(id=4, box=[0.50, 0.312, 0.70, 0.322], text="Арматура")]
    g = label_groups(L)
    assert [q["ids"] for q in g] == [[1, 2]] and g[0]["ru"] == "Бетон класса B20 для создания уклона"


def test_consistency_report():
    from drawtrans.qa import consistency as QC
    groups = {"итого": {"小计": [(1, "Итого", "小计")] * 3, "合计": [(5, "Итого", "合计")]},
              "бетон": {"混凝土": [(1, "Бетон", "混凝土")]}}
    rows = QC.report(groups)
    assert len(rows) == 1 and rows[0]["variants"][0]["zh"] == "小计" and rows[0]["variants"][1]["pages"] == [5]


def test_page_notes_normalize():
    from drawtrans.llm.tasks import page_notes as PN
    out = {"sheet_code": "", "format": "А3х3 (420x891)", "title_ru": "", "title_zh": "尺寸 2,5", "issues": [],
           "notes": [{"no": "1", "ru": "толщина 2,5", "zh": "厚度 2,5"}],
           "labels": [{"ru": "Км1", "zh": "柱 Км7"}, {"ru": "Узел", "zh": "节点"}]}
    n = PN.normalize({"data": {}, "drop_bad_labels": True}, out)
    assert n["format"] == "A3x3" and n["notes"][0]["zh"] == "厚度 2.5" and n["title_zh"] == "尺寸 2.5"
    assert [lb["ru"] for lb in n["labels"]] == ["Узел"]
    assert PN.normalize({"data": {}}, out)["labels"] == out["labels"]


def test_notes_reuse_keeps_title_block_only():
    from drawtrans.pipeline.stages_scan import _notes_reuse
    notes = ["1. За отметку 0.000 принята отметка чистого пола здания, что соответствует абсолютной отметке"]

    def pl(pg, txt):
        return {"data": {"page": pg, "expect_notes": True, "regions": [
            {"kind": "title_block", "images": [0], "ocr": ["Разраб."]},
            {"kind": "notes", "images": [1], "ocr": txt}]}, "images": [f"tb{pg}.png", f"n{pg}.png"]}
    ps = [pl(1, notes), pl(2, notes), pl(3, ["совсем другой текст примечаний на этом листе чертежа " * 3])]
    reuse = _notes_reuse([1, 2, 3], ps, "haiku")
    assert reuse == {2: 1}
    assert ps[1]["model"] == "haiku" and ps[1]["images"] == ["tb2.png"]
    assert [r["kind"] for r in ps[1]["data"]["regions"]] == ["title_block"] and "model" not in ps[0]


# ------------------------------------------------------------------ end to end (fake LLM, no OCR): zh -> en text layer
@pytest.mark.e2e
def test_zh_en_text_route_end_to_end(tmp_path):
    job = tmp_path / "zh_job"
    job.mkdir()
    shutil.copy(ROOT / "demo" / "multilang" / "zh_text.pdf", tmp_path / "zh_text.pdf")
    cfg = (ROOT / "demo" / "multilang" / "zh_job" / "project.toml").read_text(encoding="utf-8")
    cfg = cfg.replace('backend = "claude-cli"', 'backend = "fake"')
    (job / "project.toml").write_text(cfg, encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "rudraw.py"), "run", "--no-samples"], cwd=job,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode in (0, 2), r.stdout[-2000:] + r.stderr[-2000:]
    s = json.loads((job / "work" / "job" / "run_summary.json").read_text(encoding="utf-8"))
    assert s["gates"]["output_standard"]["ok"] is True
    assert list((job / "output").glob("*.placements.json"))
