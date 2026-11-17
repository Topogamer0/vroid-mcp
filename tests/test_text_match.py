import pytest

from vroid_mcp.vision.ocr import Line, Word
from vroid_mcp.vision.text_match import AmbiguousLabel, find_label, matches, nearest


def line(text, y, x=10, h=12):
    words, cx = [], x
    for t in text.split():
        words.append(Word(t, cx, y, 8 * len(t), h))
        cx += 8 * len(t) + 6
    return Line(text, words)


PANEL = [
    line("Head Size", 100),
    line("Size", 130),
    line("Eye Size X 0,350", 160),
    line("Eye SizeY 0.400", 190),
    line("Model's Helght 161.2 cm", 220),
    line("Neck Length", 250),
]


def test_exact_multiword_label_ignores_bare_word():
    assert find_label("Size", PANEL).line_text == "Size"
    assert find_label("Head Size", PANEL).line_text == "Head Size"


def test_label_followed_by_value_matches():
    c = find_label("Eye Size X", PANEL)
    assert c.box[1] == 160


def test_x_and_y_variants_are_not_confused():
    assert find_label("Eye Size Y", PANEL).box[1] == 190
    assert find_label("Eye Size X", PANEL).box[1] == 160


def test_fuzzy_ocr_misread():
    c = find_label("Model's Height", PANEL)
    assert c is not None and c.box[1] == 220 and c.score < 1.0


def test_missing_returns_none_and_nearest_helps():
    assert find_label("Shoulder Width", PANEL) is None
    assert nearest("Neck Width", PANEL)[0].line_text == "Neck Length"


def test_partial_only_when_asked():
    lines = [line("New Open", 50)]
    assert find_label("Open", lines) is None
    assert find_label("Open", lines, partial=True).text == "Open"


def test_ambiguity_raises_and_pick_resolves():
    lines = [line("Export", 40), line("Export", 300)]
    with pytest.raises(AmbiguousLabel):
        find_label("Export", lines)
    assert find_label("Export", lines, pick="topmost").box[1] == 40
    assert len(matches("Export", lines)) == 2
