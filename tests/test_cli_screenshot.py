"""The SVG screenshot, positioned so that any renderer agrees with it."""

import re
from xml.etree import ElementTree

import pytest

from labmon.cli.screenshot import placed

_TEXT = re.compile(r"<text[^>]*>.*?</text>")


def _coordinates(svg: str) -> list[float]:
    """The x of every text element, in the order they are written."""
    found: list[str] = re.findall(r'<text[^>]*\sx="([\d.]+)"[^>]*>', svg)
    return [float(match) for match in found]


def test_a_run_becomes_one_element_for_each_character() -> None:
    # librsvg — the renderer behind most desktop image viewers — ignores
    # `textLength`, so a run laid out in the viewer's own font drifts
    # off the character grid the rest of the picture is drawn on.
    drawn = placed('<text class="r1" x="10" y="20" textLength="36.6">abc</text>')

    assert _coordinates(drawn) == [10.0, 22.2, 34.4]
    assert "textLength" not in drawn


def test_every_character_keeps_the_style_of_its_run() -> None:
    drawn = placed('<text class="r1" x="0" y="20" textLength="24.4">ab</text>')

    assert drawn.count('class="r1"') == 2
    assert drawn.count('y="20"') == 2


def test_an_element_without_a_length_is_left_alone() -> None:
    # The title is centred rather than placed on the grid, and has no
    # length to divide up.
    title = '<text class="title" text-anchor="middle" x="648" y="27">labmon</text>'

    assert placed(title) == title


def test_a_character_written_as_an_entity_counts_as_one() -> None:
    # Rich writes every space as `&#160;`, so a run of padding either
    # side of a number is entities rather than characters.
    drawn = placed('<text x="0" y="20" textLength="36.6">&#160;&#160;7</text>')

    assert _coordinates(drawn) == [0.0, 12.2, 24.4]


def test_a_character_that_has_to_stay_escaped_does() -> None:
    drawn = placed('<text x="0" y="20" textLength="36.6">a&amp;b</text>')

    assert len(_TEXT.findall(drawn)) == 3
    assert "&amp;" in drawn
    assert '<text x="12.2" y="20">&</text>' not in drawn


def test_a_double_width_character_takes_two_cells() -> None:
    # The width Rich divides by is measured in cells, not characters:
    # one wide glyph in a run would otherwise push everything after it
    # half a cell to the left.
    drawn = placed('<text x="0" y="20" textLength="36.6">漢a</text>')

    assert _coordinates(drawn) == [0.0, 24.4]


def test_a_run_that_draws_nothing_is_dropped() -> None:
    # Rich ends every line with the newline as a run of its own, placed
    # past the right edge of the picture. It has no glyph to draw
    # wherever it is put, and there are thirty of them to a screen.
    assert placed('<text x="1281" y="20" textLength="12.2">\n</text>') == ""
    assert placed('<text x="0" y="20" textLength="12.2"></text>') == ""


def test_what_comes_back_is_still_valid_xml() -> None:
    drawn = placed(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        + '<text class="r1" x="0" y="20" textLength="24.4">a&lt;b</text>'
        + "</svg>"
    )

    assert ElementTree.fromstring(drawn) is not None


@pytest.mark.parametrize("cells", [1, 5, 100])
def test_the_run_still_ends_where_it_used_to(cells: int) -> None:
    # The elements are placed on the same grid the borders and the
    # background rectangles are drawn on, so the picture is unchanged —
    # only its layout stops depending on the renderer's font.
    width = 12.2
    drawn = placed(
        f'<text x="0" y="20" textLength="{width * cells}">{"a" * cells}</text>'
    )

    assert _coordinates(drawn)[-1] == pytest.approx(width * (cells - 1))
