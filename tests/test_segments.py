"""Scan route, translation-memory side (synthetic data only): volume dedupe of OCR text, layered
TM, digit guard, code-side line mapping and the page_notes packet."""
import importlib.util
import json
from pathlib import Path

import pytest
from conftest import ROOT

from scan import ctx as C
from scan import automap as A
from scan import packets as K
from scan import segments as S
from scan import tmstore as TS

W, H = 1240, 1754                     # 300 dpi pixels of a small synthetic sheet
HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"

# (id, x0, y0, x1, y1, text) in page fractions
PAGE1 = [
    (1, 0.10, 0.100, 0.40, 0.112, "Изделие закладное МН4"),
    (2, 0.10, 0.150, 0.30, 0.162, "Труба 2219"),
    (3, 0.10, 0.200, 0.30, 0.212, "Отверстие 234"),
    (4, 0.10, 0.250, 0.20, 0.262, "Км 12"),
    (5, 0.10, 0.300, 0.20, 0.312, "шт./pcs.4"),
    (6, 0.10, 0.350, 0.30, 0.362, "Tp./pipe 2219"),
    (7, 0.10, 0.500, 0.30, 0.512, "Примечания"),
    (8, 0.10, 0.530, 0.80, 0.542, "1 Бетон класса В25 принять по проекту конструкций"),
    (9, 0.10, 0.552, 0.70, 0.564, "согласно требованиям нормативных документов проекта"),
    (10, 0.10, 0.574, 0.80, 0.586, "2 Арматуру класса А500С применять по ГОСТ 34028-2016"),
    (11, 0.60, 0.900, 0.70, 0.912, "Разраб."),
    (12, 0.60, 0.930, 0.70, 0.942, "Проверил"),
    (13, 0.75, 0.900, 0.95, 0.912, "Изделие закладнoе МН4"),        # Latin o inside a Cyrillic word
]
RAPID1 = [
    (0.10, 0.100, 0.40, 0.112, "Изделие закладное МН4"),
    (0.10, 0.150, 0.30, 0.162, "Труба Ø219"),
    (0.10, 0.200, 0.30, 0.212, "Отверстие 034"),
    (0.10, 0.250, 0.20, 0.262, "Км1.2"),
    (0.10, 0.350, 0.30, 0.362, "Тр./pipe Ø219"),
    (0.50, 0.300, 0.62, 0.312, "Разрез 1-1"),                          # only RapidOCR saw it
]
PAGE2 = [
    (1, 0.10, 0.100, 0.40, 0.112, "Изделие закладноe МН5"),            # same text, OCR variant
    (2, 0.10, 0.150, 0.40, 0.162, "Издeлие закладнон МН5"),            # fuzzy variant
    (3, 0.60, 0.900, 0.70, 0.912, "Разраб."),
]


def _write_page(work: Path, pg: int, lines, rapid):
    for sub in ("lines", "rapid", "ocr", "png", "map", "notes"):
        (work / sub).mkdir(parents=True, exist_ok=True)
    with open(work / "lines" / f"p{pg:02d}.tsv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("id\tx0\ty0\tx1\ty1\tcyr\ttext\n")
        for i, x0, y0, x1, y1, t in lines:
            fh.write(f"{i}\t{x0}\t{y0}\t{x1}\t{y1}\t{1 if S.cyr_count(t) >= 2 else 0}\t{t}\n")
    with open(work / "rapid" / f"p{pg:02d}.tsv", "w", encoding="utf-8", newline="\n") as fh:
        for x0, y0, x1, y1, t in rapid:
            fh.write(f"{int(x0 * W)}\t{int(y0 * H)}\t{int(x1 * W)}\t{int(y1 * H)}\t{t}\t0.95\t0\t{W}\t{H}\n")
    (work / "ocr" / f"p{pg:02d}.tsv").write_text(HEAD + f"1\t1\t0\t0\t0\t0\t0\t0\t{W}\t{H}\t-1\t\n", encoding="utf-8")
    from PIL import Image, ImageDraw
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    for _, x0, y0, x1, y1, _t in lines:
        d.rectangle([x0 * W, y0 * H, x1 * W, y1 * H], outline=0)
    im.save(work / "png" / f"p{pg:02d}.png")


@pytest.fixture()
def proj(tmp_path):
    work = tmp_path / "work"
    _write_page(work, 1, PAGE1, RAPID1)
    _write_page(work, 2, PAGE2, [(0.10, 0.100, 0.40, 0.112, "Изделие закладное МН5")])
    cfg = C.ScanConfig(root=tmp_path, work=work, output=tmp_path / "out", names=["Борисова"])
    return C.Ctx(cfg)


def _tm(tmp_path, **kw):
    tm = TS.TMStore({"project": str(tmp_path / "tm" / "project.json")}, root=tmp_path, **kw)
    for key, zh in [("Изделие закладное МН⟨1⟩", "预埋件 МН⟨1⟩"), ("Труба Ø⟨1⟩", "钢管 Ø⟨1⟩"),
                    ("Отверстие Ø⟨1⟩", "孔 Ø⟨1⟩"), ("Км ⟨1⟩", "柱 Км⟨1⟩"), ("Разрез ⟨1⟩-⟨2⟩", "剖面 ⟨1⟩-⟨2⟩"),
                    ("Тр. / pipe Ø⟨1⟩", "钢管 Ø⟨1⟩")]:
        tm.add(key, zh, src="human")
    return tm


# ---------------------------------------------------------------- normalisation / digit guard
def test_normalise_key():
    assert S.make_key("Издeлие  закладнoе МН4 ;")[0] == "Изделие закладное МН⟨1⟩"     # Latin e/o -> Cyrillic
    assert S.make_key("Tpy6a/Pipe @219x4 FOCT/СОЗТ 10704-91")[0] == "Труба / Pipe Ø⟨1⟩x⟨2⟩ ГОСТ / GOST ⟨3⟩-⟨4⟩"
    assert S.norm("Сталь ёмкости") == "Сталь емкости"
    assert S.fold("Н. контр.") == S.fold("Н.контр")
    assert S.lookalike_cyr("Macca") == "Масса" and S.lookalike_cyr("Weight") is None


def test_reconcile_diameter_confusion():
    assert S.reconcile("Труба 2219", ["Труба Ø219"]) == ("Труба Ø219", [True])
    assert S.reconcile("Отв. G34", ["Отв. 034"]) == ("Отв. Ø34", [True])
    assert S.reconcile("Отв. 234", ["Отв. 034"])[0] == "Отв. Ø34"
    assert S.reconcile("@220мм", ["2220мм"]) == ("Ø220мм", [True])
    assert S.reconcile("2219", ["2219"]) == ("2219", [True])           # identical readings stay as read
    assert S.reconcile("Км 12", ["Км1.2"])[1] == [False]               # dropped decimal point: not confirmed
    assert S.reconcile("Блок 017", ["Блок О17"])[1] == [True]          # letter O for zero in the other reading
    assert S.reconcile("Лист 5", [])[1] == [False]


def test_fuzzy_similarity_rules():
    assert S.similar(S.skeleton("Утверждено для строительства"), S.skeleton("Утверждено для строитепьства"))
    assert not S.similar(S.skeleton("лист"), S.skeleton("лис"))                    # too short
    assert not S.similar(S.skeleton("и из стали класса прочности"), S.skeleton("стали класса прочности"))
    assert S.sig("Труба Ø⟨1⟩") != S.sig("Труба ⟨1⟩")                               # Ø is part of the number


# ---------------------------------------------------------------- volume collection
def test_collect_dedupes_volume(proj):
    seg = S.collect(proj)
    st = seg.stats
    assert (proj.w / "seg" / "segments.json").exists()
    assert st["tess_lines"] == 15        # count label line excluded; Latin-read «Tp./pipe» recovered as Russian
    assert st["after_fuzzy"] < st["unique_keys"]
    g = seg.find("Изделие закладное МН⟨1⟩")
    assert g and g["n"] >= 6 and len(g["variants"]) >= 2   # МН4 / МН5 / OCR variants in one segment
    assert seg.find("Издeлие закладнон МН⟨1⟩") is g
    rz = seg.find("Разрез ⟨1⟩-⟨2⟩")
    assert rz["need"] and rz["occ"][0]["src"] == "rapid"      # RapidOCR-only text is needed
    covered = seg.find("Труба Ø⟨1⟩")
    assert covered["need"]
    assert S.Segments.load(proj.w / "seg" / "segments.json").find("Км ⟨1⟩") is not None


# ---------------------------------------------------------------- TM store
def test_tmstore_layers_and_llm_handoff(proj, tmp_path):
    shared = tmp_path / "shared.json"
    shared.write_text(json.dumps({"Примечания": "说明", "Проверил": "审核(共享)"}, ensure_ascii=False), encoding="utf-8")
    tm = TS.TMStore({"project": "tm/project.json", "shared": [str(shared)]}, root=tmp_path, names=["Борисова"])
    assert tm.lookup("Разраб.").zh == "编制" and tm.lookup("Разраб.").layer == "glossary"
    assert tm.lookup("Проверил").layer == "shared"
    tm.add("Проверил", "校核", src="human")
    assert tm.lookup("Проверил").zh == "校核"                      # project wins
    h = tm.lookup("Лист", "tb")
    assert h.zh == "张号" and tm.lookup("Лист", "body").zh == "钢板"    # @tb| / @body| context
    tm.add("Изделие закладное МН⟨1⟩", "预埋件 МН⟨1⟩", src="human")
    f = tm.lookup("Изделие закпадное МН⟨1⟩")
    assert f and f.fuzzy and f.zh == "预埋件 МН⟨1⟩"
    assert tm.lookup("Изделие закладное МН⟨1⟩-⟨2⟩") is None          # different number layout: no fuzzy hit
    assert tm.rule("Бописова") == "keep" and tm.rule("ГОСТ ⟨1⟩-⟨2⟩") == "keep" and tm.rule("Км ⟨1⟩") is None

    seg = S.collect(proj, write=False)
    items = tm.misses(seg)
    keys = {it["key"] for it in items}
    assert "Труба Ø⟨1⟩" in keys and "Разраб." not in keys and "Изделие закладное МН⟨1⟩" not in keys
    it = next(i for i in items if i["key"] == "Труба Ø⟨1⟩")
    assert set(it) >= {"id", "key", "sample", "ctx", "n"} and len(it["ctx"]) == 2
    payloads, ids = tm.batches(items, size=3, background="test")
    assert len(payloads) == len(ids) == -(-len(items) // 3)
    assert all(not k.startswith("_") for p in payloads for i in p["data"]["items"] for k in i)
    errs = tm.ingest([{"items": [
        {"id": it["id"], "action": "translate", "zh": "钢管 Ø⟨1⟩", "conf": "high"},
        {"id": next(i["id"] for i in items if i["key"] == "Км ⟨1⟩"), "action": "translate", "zh": "柱 Км12"},
    ]}], model="fake")
    assert len(errs) == 1 and "placeholder" in errs[0]                # bare digits / missing ⟨1⟩ rejected
    e = tm.lookup("Труба Ø⟨1⟩")
    assert e.src == "llm" and e.zh == "钢管 Ø⟨1⟩"
    p = tm.save()
    tm2 = TS.TMStore({"project": str(p)}, root=tmp_path)
    assert tm2.lookup("Труба Ø⟨1⟩").zh == "钢管 Ø⟨1⟩"
    ent = json.loads(p.read_text(encoding="utf-8"))["entries"]
    assert any(v.get("model") == "fake" and v["src"] == "llm" for v in ent.values())


def test_validate_rejects_bad_answers():
    assert TS.validate("Труба Ø⟨1⟩", "钢管 Ø219") is not None
    assert TS.validate("Труба Ø⟨1⟩", "钢管 Ø⟨1⟩") is None
    assert TS.validate("Изделие закладное МН⟨1⟩", "预埋件 МН⟨1⟩") is None    # code copied from the key
    assert TS.validate("Труба", "钢管 сталь") is not None                  # Cyrillic not taken from the key
    assert TS.validate("Труба", "Truba") is not None                       # no Chinese


# ---------------------------------------------------------------- automap
def test_automap_page(proj, tmp_path):
    tm = _tm(tmp_path)
    notes = {"sheet_code": "X-1", "format": "A4", "title_ru": "Изделие закладное МН4", "title_zh": "预埋件 МН4",
             "notes": [{"no": "1", "ru": "Бетон класса В25 принять по проекту", "zh": "混凝土 B25"},
                       {"no": "2", "ru": "Арматуру класса А500С применять", "zh": "钢筋 А500С"}],
             "labels": [], "issues": []}
    m, meta, nj = A.map_page(proj, 1, tm, notes)
    L = m["lines"]
    assert L["1"] == "预埋件 МН4" and meta["lines"]["1"]["conf"] == "high"
    assert L["2"] == "钢管 Ø219"                                  # 2219 / Ø219 reconciled
    assert L["3"] == "孔 Ø34"
    assert L["4"] == "柱" and meta["lines"]["4"]["conf"] == "low"    # Км 12 vs Км1.2: number dropped
    assert "digit guard" in meta["lines"]["4"]["why"]
    assert L["5"] == ""                                            # count label left to the engine
    assert L["6"] == "钢管 Ø219"                                   # Latin-looking line, RapidOCR read Russian
    assert L["7"] == A.APPX_ZH and m["appendix"] is True
    assert L["8"] == L["9"] == L["10"] == ""
    assert L["11"] == "编制" and L["13"] == "预埋件 МН4"
    assert not any(z in ("根", "件", "间距") for z in L.values())
    assert len(m["extra"]) == 1 and m["extra"][0]["zh"] == "剖面" and m["extra"][0]["box"][0] == pytest.approx(0.5, abs=0.01)
    assert meta["extra"][0]["conf"] == "low"
    assert nj["blocks"][0]["kind"] == "notes" and len(nj["blocks"][0]["items"]) == 2 and nj["title_zh"] == "预埋件 МН4"
    # a label read from the image confirms the number
    notes["labels"] = [{"ru": "Разрез 1-1", "zh": "剖面 1-1"}]
    m2, meta2, _ = A.map_page(proj, 1, tm, notes)
    assert m2["extra"][0]["zh"] == "剖面 1-1" and meta2["extra"][0]["conf"] == "high"


def test_automap_write_files(proj, tmp_path):
    tm = _tm(tmp_path)
    st = A.write(proj, tm, {1: {"title_zh": "预埋件", "notes": [], "labels": []}})
    assert proj.map(1).exists() and proj.f("map", 1, ".meta.json").exists() and proj.notes(1).exists()
    m = C.load_map(proj, 1)
    assert m["appendix"] is False and m["lines"]["7"] != A.APPX_ZH       # no notes answer: no notes block
    assert st["lines"] > 0 and not proj.notes(2).exists()
    m2 = json.loads(proj.map(2).read_text(encoding="utf-8"))
    meta2 = json.loads(proj.f("map", 2, ".meta.json").read_text(encoding="utf-8"))
    assert m2["lines"]["1"] == "预埋件 МН5"                         # both engines read МН5
    assert m2["lines"]["2"] == "预埋件" and meta2["lines"]["2"]["conf"] == "low"   # fuzzy, number unconfirmed


# ---------------------------------------------------------------- page_notes packet
def test_page_notes_packet(proj, tmp_path):
    P = S.load_page(proj, 1)
    regs = K.notes_regions(P, K.title_block(P))
    assert len(regs) == 1 and regs[0]["head"] == 7 and set(regs[0]["lines"]) == {8, 9, 10}
    pk = K.page_notes(proj, 1, tm=_tm(tmp_path))
    kinds = [r["kind"] for r in pk["data"]["regions"]]
    assert kinds[:2] == ["title_block", "notes"] and pk["data"]["expect_notes"]
    assert "number_check" in kinds                                   # Км 12 / Км1.2 goes to the image check
    from PIL import Image
    assert pk["images"] and all(max(Image.open(f).size) <= K.MAXPX for f in pk["images"])
    tbr = pk["data"]["regions"][0]
    assert any("Разраб" in t for t in tbr["ocr"])
    json.dumps(pk["data"], ensure_ascii=False)                       # payload is JSON-able


def test_audit_name_skip_is_fuzzy():
    spec = importlib.util.spec_from_file_location("audit30", ROOT / "scripts" / "scan" / "30_audit.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    skip = m.make_auto_skip(C.ScanConfig(names=["Борисова"]))
    assert skip("Бописова") == "人名/标识"                             # п read for р
    assert skip("Борисова") == "人名/标识"
    assert skip("Изделие закладное") is None
