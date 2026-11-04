import pymupdf
import pytest
from engine import layout, place
from engine.layout import from_frame_rect, to_frame_rect, word_lang
from engine.place import NO_LINE_START, Obstacles, fit, text_len, wrap


@pytest.mark.parametrize("word,lang", [
    ("Бетон", "ru"), ("фундамента", "ru"), ("concrete", "en"),
    ("ГОСТ", "neu"), ("А500С", "neu"), ("Фм1", "neu"), ("L=2350", "neu"), ("2400", "neu"),
    ("мм", "neu"), ("КП", "neu"),                 # unit, short upper-case code
    ("кг/kg", "mix"),
])
def test_word_language(word, lang):
    assert word_lang(word) == lang


def test_configured_names_are_neutral():
    assert word_lang("Иванов") == "ru"
    layout.NAMES.add("Иванов")
    try:
        assert word_lang("Иванов") == "neu"
    finally:
        layout.NAMES.discard("Иванов")


@pytest.mark.parametrize("o", ["h", "up", "down"])
def test_frame_round_trip(o):
    r = pymupdf.Rect(10, 20, 110, 32)
    assert from_frame_rect(to_frame_rect(r, o), o) == r


def hang(line):
    return line[:-1] if line[-1:] in NO_LINE_START else line


TEXT = "基础混凝土强度等级 B25，抗渗等级 W6，抗冻等级 F150，执行 ГОСТ 26633-2015。"


@pytest.mark.parametrize("width", [60, 90, 150, 400])
def test_wrap_never_exceeds_the_slot(width):
    fs = 7
    lines = wrap(TEXT, [width] * 20, fs)
    assert lines is not None
    for ln in lines:
        # a closing ，。 may hang past the edge (CJK hanging punctuation) rather than start a line
        assert text_len(hang(ln), fs) <= width + 0.01
        assert ln[:1] not in NO_LINE_START
    assert "".join(lines).replace(" ", "") == TEXT.replace(" ", "")
    # Latin/number tokens are never split across lines
    assert any("26633-2015" in ln for ln in lines)


def test_wrap_reports_when_it_does_not_fit():
    assert wrap(TEXT, [60, 60], 7) is None                  # too few lines
    assert wrap("26633-2015", [10], 7) is None              # one token wider than the slot


def test_fit_shrinks_the_font_until_it_fits():
    slots = [pymupdf.Rect(0, 0, 120, 10)]
    fs, lines = fit("尺寸单位为 mm，标高单位为 m。", slots, base=10)
    assert fs is not None and fs < 10
    assert len(lines) == 1 and text_len(hang(lines[0]), fs) <= 120.01


@pytest.fixture
def page():
    doc = pymupdf.open()
    p = doc.new_page(width=400, height=300)
    p.insert_text((50, 100), "Some text here", fontsize=10)
    p.draw_line((50, 200), (350, 200))
    return p


def test_obstacles_free_space(page):
    words = layout.page_words(page)
    obs = Obstacles(page, words, layout.page_segments(page))
    over_text = pymupdf.Rect(55, 92, 90, 100)
    empty = pymupdf.Rect(50, 130, 200, 145)
    on_rule = pymupdf.Rect(50, 195, 200, 205)
    off_page = pymupdf.Rect(380, 130, 450, 145)
    assert not obs.free(over_text, "h")
    assert obs.free(empty, "h")
    assert not obs.free(on_rule, "h")
    assert obs.free(on_rule, "h", segs=False)
    assert not obs.free(off_page, "h")
    # the words of the unit being placed can be ignored
    assert obs.free(over_text, "h", ignore={id(w) for w in words})


def test_font_is_available():
    assert place.FONT.has_glyph(ord("混"))
