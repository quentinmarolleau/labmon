"""The panel's SVG screenshot, positioned so every renderer agrees.

Rich writes one `<text>` element per run of identically styled
characters and holds each one to the width it should occupy with the
SVG `textLength` attribute. A browser honours that, which is where the
format is usually looked at, and librsvg does not — it ignores both
`textLength` and a list of coordinates, and lays the run out in whatever
monospace font it found, at whatever advance width that font has.

That matters because librsvg is what most of the desktop opens an SVG
with: GNOME's image viewer and file previews, ImageMagick, GIMP. A run
drifts from the character grid the box rules and the background
rectangles are drawn on, so a right-aligned column of readings comes
out ragged against its own border and the rules break into dashes — the
one thing a screenshot of a table is taken to show.

One element per character is the only positioning every renderer
honours. It is the same picture: the coordinates come from the width
Rich already computed, so nothing moves, and what was a browser-only
image becomes one that can be pasted into an issue.
"""

import html
import re
from xml.sax.saxutils import escape

from rich.cells import cell_len

_TEXT = re.compile(r"<text([^>]*)>(.*?)</text>", re.DOTALL)
_LENGTH = re.compile(r'\s+textLength="([\d.]+)"')
_START = re.compile(r'\s+x="([\d.]+)"')


def placed(svg: str) -> str:
    """`svg` with every character given a coordinate of its own."""
    return _TEXT.sub(_characters, svg)


def _characters(run: "re.Match[str]") -> str:
    """One run of styled text, as one element per character.

    An element carrying no length is left exactly as it was: the title
    is centred on the picture rather than placed on the grid, and there
    is nothing to divide up.

    A run that occupies no cells is dropped. Rich ends every line with
    the newline itself as a run of its own, placed past the right edge
    of the picture, and a newline has no glyph to draw wherever it is
    put — thirty elements a screen, saying nothing.

    The division is by cells rather than by characters, which is what
    Rich measured the length in. A double-width glyph counts twice, and
    counting it once would pull everything after it half a cell left.
    """
    attributes, content = run.group(1), run.group(2)
    length = _LENGTH.search(attributes)
    start = _START.search(attributes)
    if length is None or start is None:
        return run.group(0)

    characters = html.unescape(content)
    cells = cell_len(characters)
    if not cells:
        return ""

    cell = float(length.group(1)) / cells
    left = float(start.group(1))
    common = _LENGTH.sub("", attributes)

    written: list[str] = []
    taken = 0
    for character in characters:
        at = _START.sub(f' x="{left + taken * cell:.2f}"', common, count=1)
        written.append(f"<text{at}>{escape(character)}</text>")
        taken += cell_len(character)
    return "".join(written)
