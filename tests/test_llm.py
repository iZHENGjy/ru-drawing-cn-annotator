"""LLM layer: cache replay, retries, budget, ledger, validators (fake backend only, no network)."""
import json
import time

import pytest

from llm import Session, get_task
from llm.backends import FakeBackend, Reply
from llm.session import LEDGER_FIELDS
from llm import validate as V


def pt_payload(items):
    return {"data": {"items": items, "terms": {"Арматура": "钢筋"}}, "images": []}


ITEMS = [{"id": "a1", "key": "Арматура А⟨1⟩С", "sample": "Арматура А500С", "ctx": ["", ""], "n": 2},
         {"id": "a2", "key": "Разрез ⟨1⟩-⟨2⟩", "sample": "Разрез 1-1", "ctx": ["", ""], "n": 1},
         {"id": "a3", "key": "Иванов", "sample": "Иванов", "ctx": ["", ""], "n": 1}]
GOOD = {"a1": ("translate", "钢筋 А⟨1⟩С"), "a2": ("translate", "剖面 ⟨1⟩-⟨2⟩"), "a3": ("keep", "")}


def answer(items, override=None):
    out = []
    for it in items:
        act, zh = (override or {}).get(it["id"], GOOD[it["id"]])
        out.append({"id": it["id"], "action": act, "zh": zh, "conf": "high"})
    return {"items": out}


def ledger(work):
    p = work / "job" / "ledger.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


CFG = {"backend": "fake", "concurrency": 2, "budget_usd": 5, "background": "test project"}


# ------------------------------------------------------------------ cache / ledger
def test_cache_replay_identical_and_free(tmp_path):
    fb = FakeBackend(lambda req: answer(req.payload["data"]["items"]), cost_usd=0.002)
    r1 = Session(tmp_path, CFG, "translate", backend=fb).run("phrase_translate", [pt_payload(ITEMS)], ["b1"])
    assert r1[0].ok and not r1[0].cached and r1[0].cost_usd == pytest.approx(0.002)
    fb2 = FakeBackend(lambda req: pytest.fail("cache miss: backend called"))
    r2 = Session(tmp_path, CFG, "translate", backend=fb2).run("phrase_translate", [pt_payload(ITEMS)], ["b1"])
    assert r2[0].ok and r2[0].cached and r2[0].cost_usd == 0
    assert r2[0].output == r1[0].output
    rows = ledger(tmp_path)
    assert [r["cached"] for r in rows] == [False, True]
    assert rows[1]["cost_usd"] == 0 and rows[1]["orig_cost_usd"] == pytest.approx(0.002)
    assert all(list(r) == list(LEDGER_FIELDS) for r in rows)
    assert rows[0]["stage"] == "translate" and rows[0]["task"] == "phrase_translate" and rows[0]["item"] == "b1"
    assert rows[0]["model"] == "sonnet" and rows[0]["backend"] == "fake" and rows[0]["images"] == 0


def test_cache_key_changes_with_data_model_and_background(tmp_path):
    fb = FakeBackend(lambda req: answer(req.payload["data"]["items"]))
    Session(tmp_path, CFG, "t", backend=fb).run("phrase_translate", [pt_payload(ITEMS)], ["b1"])
    Session(tmp_path, {**CFG, "models": {"phrase_translate": "haiku"}}, "t", backend=fb).run(
        "phrase_translate", [pt_payload(ITEMS)], ["b1"])
    Session(tmp_path, {**CFG, "background": "other"}, "t", backend=fb).run("phrase_translate", [pt_payload(ITEMS)], ["b1"])
    Session(tmp_path, CFG, "t", backend=fb).run("phrase_translate", [pt_payload(ITEMS[:2])], ["b1"])
    assert len(fb.calls) == 4


def test_cache_key_includes_image_bytes(tmp_path):
    img = tmp_path / "t1.png"
    img.write_bytes(b"\x89PNG one")
    pl = {"data": {"page": 1, "tiles": [{"id": "t1", "ocr": ["Разрез 1-1"]}]}, "images": [str(img)]}
    fb = FakeBackend()
    s = Session(tmp_path / "w", CFG, "visual", backend=fb)
    assert s.run("visual_review", [pl], ["p01"])[0].ok
    assert s.run("visual_review", [pl], ["p01"])[0].cached
    img.write_bytes(b"\x89PNG two")
    assert not s.run("visual_review", [pl], ["p01"])[0].cached
    assert len(fb.calls) == 2 and fb.calls[0].images and ledger(tmp_path / "w")[0]["images"] == 1


# ------------------------------------------------------------------ retries
def test_retry_feeds_errors_back_and_retries_only_bad_items(tmp_path):
    def resp(req):
        items = req.payload["data"]["items"]
        if req.attempt == 1:
            return answer(items, {"a2": ("translate", "剖面 1-1")})        # bare digits
        return answer(items)
    fb = FakeBackend(resp)
    r = Session(tmp_path, CFG, "translate", backend=fb).run("phrase_translate", [pt_payload(ITEMS)], ["b1"])[0]
    assert r.ok and r.attempts == 2
    assert {o["id"] for o in r.output["items"]} == {"a1", "a2", "a3"}
    second = fb.calls[1]
    assert [it["id"] for it in second.payload["data"]["items"]] == ["a2"]           # only the bad item
    assert "rejected by the checker" in second.text and "[a2]" in second.text and "剖面 1-1" in second.text
    rows = ledger(tmp_path)
    assert [(x["attempt"], x["ok"]) for x in rows] == [(1, False), (2, True)]
    assert (tmp_path / "llm" / "errors.jsonl").exists()


def test_retry_exhausted_is_never_accepted(tmp_path):
    fb = FakeBackend(lambda req: answer(req.payload["data"]["items"], {"a1": ("translate", "钢筋 А500С")}))
    r = Session(tmp_path, CFG, "translate", backend=fb).run("phrase_translate", [pt_payload(ITEMS)], ["b1"])[0]
    assert not r.ok and r.attempts == 3 and len(fb.calls) == 3
    assert any(e.startswith("[a1]") for e in r.errors)
    assert not list((tmp_path / "llm" / "cache").glob("*.json"))                   # not cached
    assert [x["ok"] for x in ledger(tmp_path)] == [False, False, False]


def test_backend_error_is_retried(tmp_path):
    def resp(req):
        if req.attempt == 1:
            return Reply(error="timeout after 600s")
        return answer(req.payload["data"]["items"])
    r = Session(tmp_path, CFG, "t", backend=FakeBackend(resp)).run("phrase_translate", [pt_payload(ITEMS)], ["b1"])[0]
    assert r.ok and r.attempts == 2


def test_full_retry_for_global_error(tmp_path):
    def resp(req):
        if req.attempt == 1:
            return {"items": "nope"}
        return answer(req.payload["data"]["items"])
    fb = FakeBackend(resp)
    r = Session(tmp_path, CFG, "t", backend=fb).run("phrase_translate", [pt_payload(ITEMS)], ["b1"])[0]
    assert r.ok and len(fb.calls[1].payload["data"]["items"]) == 3 and "Previous answer" in fb.calls[1].text


# ------------------------------------------------------------------ budget
def test_budget_refuses_when_ledger_is_spent(tmp_path):
    (tmp_path / "job").mkdir()
    (tmp_path / "job" / "ledger.jsonl").write_text(json.dumps({"cost_usd": 0.999}) + "\n", encoding="utf-8")
    fb = FakeBackend(lambda req: answer(req.payload["data"]["items"]))
    rs = Session(tmp_path, {**CFG, "budget_usd": 1.0}, "t", backend=fb).run(
        "phrase_translate", [pt_payload(ITEMS)], ["b1"])
    assert not rs[0].ok and rs[0].errors == ["budget"] and not fb.calls
    assert len(ledger(tmp_path)) == 1                                             # no row for a refused call


def test_budget_stops_mid_run(tmp_path):
    fb = FakeBackend(lambda req: answer(req.payload["data"]["items"]), cost_usd=0.001)
    cfg = {**CFG, "concurrency": 1, "budget_usd": 0.0024, "models": {"phrase_translate": "haiku"}}
    rs = Session(tmp_path, cfg, "t", backend=fb).run("phrase_translate", [pt_payload(ITEMS)] * 1 + [
        pt_payload(ITEMS[:2]), pt_payload(ITEMS[:1])], ["b1", "b2", "b3"])
    assert rs[0].ok and [r.errors for r in rs[1:]] == [["budget"], ["budget"]]
    assert len(fb.calls) == 1


# ------------------------------------------------------------------ concurrency
def test_concurrency_results_in_input_order(tmp_path):
    def resp(req):
        time.sleep(0.02 * (5 - int(req.payload["data"]["items"][0]["id"][1:]) % 5))
        return answer(req.payload["data"]["items"])
    pls, ids = [], []
    for k in range(10):
        it = {**ITEMS[1], "id": f"x{k}", "key": f"Разрез ⟨1⟩-⟨2⟩ вид{k}"}
        GOOD[f"x{k}"] = ("translate", f"剖面 ⟨1⟩-⟨2⟩ 视图{'甲乙丙丁戊己庚辛壬癸'[k]}")
        pls.append(pt_payload([it])); ids.append(f"b{k}")
    rs = Session(tmp_path, {**CFG, "concurrency": 4}, "t", backend=FakeBackend(resp)).run("phrase_translate", pls, ids)
    assert [r.id for r in rs] == ids
    assert [r.output["items"][0]["id"] for r in rs] == [f"x{k}" for k in range(10)]
    assert len(ledger(tmp_path)) == 10


def test_default_fake_answers_are_valid_for_every_task(tmp_path):
    s = Session(tmp_path, CFG, "t")
    pls = {"phrase_translate": pt_payload(ITEMS),
           "page_notes": {"data": {"page": 3, "regions": [{"kind": "notes", "ocr": ["1. Бетон B25"]}]}},
           "audit_review": {"data": {"packet": "## 第3页\n#1 [未覆盖] 识别:「Разрез 1-1」  box=[1, 2, 3, 4]\n"
                                               "C1 「вид А」 box=[5, 6, 7, 8]  紧邻行「Разрез」→已译「剖面」\n"}},
           "visual_review": {"data": {"tiles": [{"id": "t1", "ocr": []}, {"id": "t2", "ocr": []}]}}}
    for task, pl in pls.items():
        r = s.run(task, [pl], ["p03"])[0]
        assert r.ok, (task, r.errors)


def test_unknown_task_and_bad_ids():
    with pytest.raises(KeyError):
        get_task("nope")
    with pytest.raises(ValueError):
        Session("w", CFG, "t", backend=FakeBackend()).run("phrase_translate", [{}, {}], ["a", "a"])


# ------------------------------------------------------------------ validators
def pt_errs(override, items=ITEMS):
    return get_task("phrase_translate").validate(pt_payload(items), answer(items, override))


def test_phrase_translate_validator():
    assert pt_errs({}) == []
    assert any("placeholder" in e for e in pt_errs({"a2": ("translate", "剖面 ⟨1⟩")}))
    assert any("bare digits" in e for e in pt_errs({"a2": ("translate", "剖面 ⟨1⟩-⟨2⟩ 2")}))
    assert any("not in key" in e for e in pt_errs({"a1": ("translate", "钢筋 В⟨1⟩С")}))
    assert any("untranslated" in e for e in pt_errs({"a2": ("translate", "Разрез ⟨1⟩-⟨2⟩ 剖面")}))
    assert any("needs Chinese" in e for e in pt_errs({"a2": ("translate", "⟨1⟩-⟨2⟩")}))
    assert any("count labels" in e for e in pt_errs({"a2": ("translate", "⟨1⟩根")}))
    out = answer(ITEMS)
    out["items"].append(dict(out["items"][0]))
    out["items"].pop(1)
    errs = get_task("phrase_translate").validate(pt_payload(ITEMS), out)
    assert "[a1] duplicate id" in errs and "[a2] missing from the answer" in errs
    assert V.bad_ids(errs) == {"a1", "a2"}
    assert get_task("phrase_translate").validate(pt_payload(ITEMS), {"items": [{"id": "a1"}]})   # shape


def test_check_against_numbers_marks_grades_standards():
    assert V.check_against("直径 Ø16 钢筋", "стержень Ø16") == []
    assert any("'12'" in e for e in V.check_against("直径 Ø12 钢筋", "стержень Ø16"))
    assert V.check_against("柱 Км1.2", "Колонна Км1.2") == []
    errs = V.check_against("柱 Км1.2", "Колонна Км 12")
    assert any("member mark" in e for e in errs)
    assert V.check_against("柱 Км1.2", "Колонна Км 12", known_marks=["Км1.2"]) == []
    assert V.check_against("柱 Км12", "Колонна Км1.2")                          # dot dropped
    assert any("grade" in e for e in V.check_against("钢筋 А400С", "Арматура А500С А400"))
    assert V.check_against("钢筋 A500C", "Арматура А500С") == []                # Latin look-alikes
    assert any("standard" in e for e in V.check_against("按 ГОСТ 34028-2017", "по ГОСТ 34028-2016"))
    assert V.check_against("按 ГОСТ 34028-2016", "по ГОСТ 34028-2016") == []
    assert V.check_against("标高 +1.250", "отм. +1,250") == []                   # decimal comma -> point
    assert any("decimal comma" in e for e in V.check_against("标高 +1,250", "отм. +1,250"))
    assert V.check_against("2016年12月", "декабрь 2016") == []
    assert V.check_against("间距 1200", "шаг 1 200") == []
    assert any("Russian" in e for e in V.check_against("见 Лист 3", "см. лист 3"))
    assert any("untranslated" in e for e in V.check_against("见 лист 3", "см. лист 3"))


def notes_out(**kw):
    o = {"sheet_code": "XX-KM.001", "format": "A1", "title_ru": "Схема колонн Км1.1…Км1.3",
         "title_zh": "柱 Км1.1…Км1.3 布置图", "notes": [{"no": "1", "ru": "Бетон класса B25, W8.", "zh": "B25 级混凝土,W8。"}],
         "labels": [{"ru": "Разрез 1-1", "zh": "剖面 1-1"}], "issues": []}
    o.update(kw)
    return o


def test_page_notes_validator():
    v = get_task("page_notes").validate
    pl = {"data": {"page": 1, "regions": []}}
    assert v(pl, notes_out()) == []
    assert any(e.startswith("[note 1]") for e in v(pl, notes_out(notes=[{"no": "1", "ru": "Бетон B25", "zh": "B30 混凝土"}])))
    assert any(e.startswith("[title]") for e in v(pl, notes_out(title_zh="柱 Км12 布置图")))
    assert any("issues" in e for e in v(pl, notes_out(labels=[{"ru": "Раз[?]ез", "zh": "剖[?]"}])))
    assert any("format" in e for e in v(pl, notes_out(format="big")))
    assert any("empty zh" in e for e in v(pl, notes_out(labels=[{"ru": "Вид А", "zh": ""}])))
    assert any("missing field" in e for e in v(pl, {k: x for k, x in notes_out().items() if k != "issues"}))
    # the clause number itself is allowed in zh
    assert v(pl, notes_out(notes=[{"no": "2", "ru": "См. п.1", "zh": "见第1条(第2条)"}])) == []


PACKET = ("## 第5页  柱布置图  (页面 2384x1684pt)\n\n"
          "#1 [未覆盖] 识别:「для Рм2.2」  box=[100.0, 200.0, 180.0, 210.0]\n"
          "   邻近: 「Балка Бм2.5」→梁 Бм2.5\n"
          "#2 [空映射] 识别:「Иванов」  box=[10, 20, 30, 40]\n"
          "裁图联排: C:\\private\\work\\packets\\p05_s1.png  (含 #1–#2)\n\n"
          "### 续行核对(纯文字,无需看图):判断该俄文是否已包含在紧邻行的中文里\n"
          "C1 「условно не показана」 box=[1, 2, 3, 4]  紧邻行「Арматура Пм2」→已译「板 Пм2 钢筋」\n")


def audit_out(**over):
    items = {"#1": {"ref": "#1", "act": "tr", "ru": "для Рм2.2", "zh": "用于横梁 Рм2.2", "why": ""},
             "#2": {"ref": "#2", "act": "skip", "ru": "", "zh": "", "why": "人名"},
             "C1": {"ref": "C1", "act": "tr", "ru": "условно не показана", "zh": "按简化画法未示出", "why": ""}}
    for k, v in over.items():
        if v is None:
            items.pop(k)
        else:
            items[k] = {**items[k], **v}
    return {"items": list(items.values())}


def test_audit_review_validator_and_render():
    t = get_task("audit_review")
    pl = {"data": {"packet": PACKET}, "images": []}
    assert t.validate(pl, audit_out()) == []
    assert any("missing" in e for e in t.validate(pl, audit_out(**{"#2": None})))
    assert any("needs why" in e for e in t.validate(pl, audit_out(**{"#2": {"why": ""}})))
    assert any("needs Chinese" in e for e in t.validate(pl, audit_out(**{"#1": {"zh": ""}})))
    assert any("Рм2.3" in e for e in t.validate(pl, audit_out(**{"#1": {"ru": "для Рм2.3", "zh": "用于横梁 Рм2.3"}})))
    # box coordinates are not a source of numbers
    assert any("'100.0'" in e for e in t.validate(pl, audit_out(**{"#1": {"zh": "用于横梁 Рм2.2 100.0"}})))
    assert t.validate({"data": {"packet": PACKET, "marks": ["Рм2.3"]}}, audit_out(**{"#1": {"zh": "用于横梁 Рм2.3"}})) == []
    text, _ = t.render(pl)
    assert "private" not in text and "p05_s1.png" in text and "#1, #2, C1" in text


def test_visual_review_validator():
    t = get_task("visual_review")
    pl = {"data": {"tiles": [{"id": "t1", "ocr": ["Разрез 2-2"]}, {"id": "t2", "ocr": []}]}}
    issue = {"tile": "t1", "type": "漏译", "text": "Разрез 2-2", "where": [0.1, 0.2, 0.3, 0.25], "zh": "剖面 2-2"}
    assert t.validate(pl, {"checked": ["t1", "t2"], "issues": [issue]}) == []
    assert any("missing" in e for e in t.validate(pl, {"checked": ["t1"], "issues": []}))
    assert any("where" in e for e in t.validate(pl, {"checked": ["t1", "t2"], "issues": [{**issue, "where": [0.5, 0.2, 0.3, 0.9]}]}))
    assert any("'3'" in e for e in t.validate(pl, {"checked": ["t1", "t2"], "issues": [{**issue, "zh": "剖面 3-3"}]}))
    assert any("unknown tile" in e for e in t.validate(pl, {"checked": ["t1", "t2"], "issues": [{**issue, "tile": "t9"}]}))
    # tiles.json mapping as passed by the pipeline: ids shortened to the form 41_apply_visual expects
    pl2 = {"data": {"page": 7, "tiles": {"p07_t1": [0, 0, 1, 1], "p07_t2": [0, 0, 1, 1]}, "flags": ["遮挡"]}}
    assert t.validate(pl2, t.fake(pl2)) == [] and t.fake(pl2)["checked"] == ["t1", "t2"]
    assert any("'2'" in e for e in t.validate(pl2, {"checked": ["t1", "t2"], "issues": [{**issue, "zh": "剖面 2-2"}]}))
    assert "tile t2" in t.render(pl2)[0] and "QA flags" in t.render(pl2)[0]


def test_page_notes_render_packets_shape():
    pl = {"data": {"page": 4, "expect_notes": True, "regions": [
        {"kind": "title_block", "images": [0], "ocr": ["Схема"]},
        {"kind": "notes", "images": [1, 2], "ocr": ["1. Бетон"], "clauses": 3},
        {"kind": "number_check", "images": [3], "items": [{"n": 1, "tess": "Ø1б", "rapid": "Ø16"}]}]},
        "images": ["a.png", "b.png", "c.png", "d.png"]}
    text, imgs = get_task("page_notes").render(pl)
    assert len(imgs) == 4 and "image 2, image 3" in text and "about 3 numbered clauses" in text
    assert "OCR-1 «Ø1б» / OCR-2 «Ø16»" in text
