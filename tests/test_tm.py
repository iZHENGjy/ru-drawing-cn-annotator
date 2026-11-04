import json

import pytest
from engine.tm import TM, fmt_num, join_lines, make_key


@pytest.fixture
def tm(tmp_path):
    glossary = tmp_path / "glossary.json"
    glossary.write_text(json.dumps({
        "_comment": "ignored",
        "@ambiguous": "Лист",
        "@tb|Лист": "张号",
        "@body|Лист": "钢板",
        "Примечания": "注",
        "Бетон класса B⟨1⟩": "混凝土 B⟨1⟩",
        "Размеры даны в миллиметрах": "尺寸单位为 mm",
        "Отметки даны в метрах": "标高单位为 m",
        "Насос": "泵",
    }, ensure_ascii=False), encoding="utf-8")
    project = tmp_path / "tm.json"
    project.write_text(json.dumps({"Насос": "水泵"}, ensure_ascii=False), encoding="utf-8")
    return TM(project, [glossary])


def test_make_key_replaces_every_number():
    key, nums = make_key("Бетон класса B25 W6 F150 по ГОСТ 26633-2015")
    assert key == "Бетон класса B⟨1⟩ W⟨2⟩ F⟨3⟩ по ГОСТ ⟨4⟩-⟨5⟩"
    assert nums == ["25", "6", "150", "26633", "2015"]


def test_numbers_come_from_the_source_not_the_tm(tm):
    zh, key = tm.translate("Бетон класса B25")
    assert (zh, key) == ("混凝土 B25", "Бетон класса B⟨1⟩")
    assert tm.translate("Бетон класса B30")[0] == "混凝土 B30"


def test_decimal_comma_becomes_point_but_codes_keep_it(tm):
    assert tm.translate("Бетон класса B7,5")[0] == "混凝土 B7.5"
    assert fmt_num("1,2", "-О") == "1,2"          # code such as 1,2-О-Ч
    assert fmt_num("0,95", "") == "0.95"


def test_project_entry_overrides_glossary(tm):
    assert tm.translate("Насос")[0] == "水泵"


def test_context_for_ambiguous_keys(tm):
    assert tm.translate("Лист", "tb")[0] == "张号"
    assert tm.translate("Лист", "body")[0] == "钢板"


def test_ambiguous_key_without_context_entry_is_missing(tmp_path):
    p = tmp_path / "tm.json"
    p.write_text(json.dumps({"@ambiguous": "Лист", "Лист": "张"}, ensure_ascii=False), encoding="utf-8")
    t = TM(p)
    zh, key = t.translate("Лист", "body")
    assert zh is None and key == "@body|Лист"


def test_missing_keys_are_recorded_with_page(tm):
    tm.page = 7
    zh, key = tm.translate("Неизвестная фраза 12 мм")
    assert zh is None
    assert key == "Неизвестная фраза ⟨1⟩ мм"
    assert tm.missing[key] == "Неизвестная фраза 12 мм"
    assert tm.missing_ctx[key] == {7}


def test_trailing_separator_becomes_fullwidth(tm):
    assert tm.translate("Примечания:")[0] == "注："


def test_hyphenated_line_break_is_joined():
    assert join_lines("Приме-\nчания") == "Примечания"
    assert join_lines("Размеры даны\nв миллиметрах") == "Размеры даны в миллиметрах"


def test_numbered_clauses_are_translated_one_by_one(tm):
    zh, keys = tm.translate_lines(["1. Размеры даны в миллиметрах", "2. Отметки даны в метрах"])
    assert zh == "1. 尺寸单位为 mm\n2. 标高单位为 m"
    assert len(keys) == 2


def test_one_missing_clause_makes_the_whole_run_missing(tm):
    zh, keys = tm.translate_lines(["1. Размеры даны в миллиметрах", "2. Что-то новое"])
    assert zh is None


@pytest.mark.parametrize("key,val,ok", [
    ("Бетон класса B⟨1⟩", "混凝土 B⟨1⟩", True),
    ("Бетон класса B⟨1⟩", "混凝土 B25", False),          # bare digits: number would be frozen
    ("Бетон класса B⟨1⟩ W⟨2⟩", "混凝土 B⟨1⟩", False),   # placeholder lost
    ("Слой ⟨1⟩ мм", "", True),                          # "" = intentionally untranslated
])
def test_check_entry(key, val, ok):
    assert (TM.check_entry(key, val) is None) == ok
