"""The Textual application `labmon monitor` runs.

Thin on purpose. Everything that decides *what* to show lives in
`labmon.cli.monitor` and `labmon.cli.render`, neither of which needs an
event loop; this module gives those rows a border, a colour scheme and a
reason to happen again in two seconds.

Imported only from inside `labmon.cli.commands.monitor`, because Textual
lives behind the `tui` extra and pulls Rich and a tree of its own
dependencies. Nothing that merely writes readings should pay for that.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, tzinfo
from typing import ClassVar, final, override

from rich import box
from rich.align import Align
from rich.console import RenderableType
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Grid, Middle, VerticalScroll
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Digits, Footer, Header, Label, OptionList, Static
from textual.widgets.option_list import Option

from labmon.cli.age import Freshness
from labmon.cli.monitor import (
    Snapshot,
    Tile,
    cadences,
    seconds,
    status,
    take,
    tiles,
)
from labmon.cli.render import LATEST_HEADINGS, LatestRow

# Aliased: `Panel` here is the application, and a `[[monitor.panels]]`
# entry is the specification for one tile inside it.
from labmon.config import Display as DisplaySpec
from labmon.config import Panel as PanelSpec

# The panel's default look. Nord is calm at a glance and legible on a
# projector, which is where a panel beside an experiment tends to end
# up. It is a default, not a decision: Textual ships twenty themes and
# the command palette switches between them live.
THEME = "nord"

# Columns holding a number, right-aligned so digits line up under each
# other. A column of left-aligned floats has to be read digit by digit
# to compare two of them.
_NUMERIC: frozenset[str] = frozenset({"value", "mean", "sd", "n"})

# What each column is called on screen. The statistics are named in
# `labmon.cli.render` and shared, so the panel and the plain table
# cannot label the same number differently; `sensor` is the panel's
# own, which has the width to drop the `_id`.
_HEADINGS: dict[str, str] = {"sensor_id": "sensor", **LATEST_HEADINGS}

# How a row is tinted by how long ago it last reported. Fresh rows are
# left alone: colouring everything colours nothing.
_STATE_STYLE: dict[Freshness, str] = {
    Freshness.FRESH: "",
    Freshness.AGEING: "yellow",
    Freshness.STALE: "red",
}


def stretched(widths: Mapping[str, int], snapshot: Snapshot) -> dict[str, int]:
    """`widths`, widened wherever this tick needs more room.

    A column sized to whatever it happens to hold changes width whenever
    a reading gains or loses a digit — and every column right of it, and
    the centred table itself, shifts with it. Redrawn every two seconds
    that is a table which twitches, and a number somebody is watching is
    never quite where they last read it.

    Never narrowed, which is why the running total is kept outside the
    renderer: a column that shrinks back at the next tick is the same
    jitter in the other direction. It settles within a few ticks at the
    widest each column has genuinely needed, and a panel left open all
    day pays a couple of columns' worth of width for a table that stays
    still.

    A new mapping rather than an edit in place, so a caller holding the
    old one still has what it had.
    """
    grown = dict(widths)
    for position, name in enumerate(snapshot.columns):
        needed = max(
            [
                len(_HEADINGS.get(name, name)),
                *(len(row.cells[position]) for row in snapshot.rows),
            ]
        )
        grown[name] = max(grown.get(name, 0), needed)
    return grown


def panel(
    snapshot: Snapshot,
    *,
    window: str,
    refresh: float,
    tz: tzinfo = UTC,
    widths: Mapping[str, int] | None = None,
) -> RenderableType:
    """One tick, as something Rich can draw.

    Separate from the widget so it can be rendered to text and asserted
    against without starting an application.

    `widths` are floors, not sizes: a column still grows to hold
    something longer than the panel has seen. Without them each column
    is sized to this tick alone — see `stretched`.
    """
    held = widths or {}
    if not snapshot.rows:
        return Align.center(
            Text(
                "no readings matched"
                if snapshot.error is None
                else str(snapshot.error),
                style="dim italic",
            )
        )

    table = Table(
        box=box.ROUNDED,
        border_style="dim",
        header_style="bold",
        title=Text("labmon", style="bold"),
        caption=Text(
            status(snapshot, window=window, refresh=refresh, tz=tz), style="dim"
        ),
        expand=False,
    )
    for name in snapshot.columns:
        heading = _HEADINGS.get(name, name)
        if name in _NUMERIC:
            # Right-aligned so digits line up under each other, and
            # never wrapped: a reading split across two lines is a
            # reading nobody can compare against the one above it.
            table.add_column(
                heading,
                justify="right",
                no_wrap=True,
                min_width=held.get(name),
            )
        else:
            # Folded rather than ellipsised when the terminal is too
            # narrow. `wavemeter-1` and `wavemeter-thz` both truncate to
            # `wavemet…`, which makes two rows indistinguishable; folded
            # onto a second line they stay readable. Giving the text
            # columns the folding also keeps the numbers at full width.
            table.add_column(
                heading,
                justify="left",
                style="dim" if name == "unit" else "",
                overflow="fold",
                min_width=held.get(name),
            )

    for row, cells, ends in _grouped(snapshot.columns, snapshot.rows):
        table.add_row(*cells, style=_STATE_STYLE[row.state], end_section=ends)

    return Align.center(table)


def _grouped(
    columns: Sequence[str], rows: Sequence[LatestRow]
) -> list[tuple[LatestRow, tuple[str, ...], bool]]:
    """Each row, its cells, and whether a rule follows it.

    The rows arrive sorted by measurement, so the column repeats a word
    the row above has already said — five or six names down sixteen
    rows, in a column as wide as the longest of them. Written once per
    group it reads as a heading for the rows underneath, which is what
    it has been all along, and the rule is what says where one group
    stops: blanked repeats with nothing between them leave two groups
    touching.

    The last group is not ruled off. Its rule would land against the
    bottom border of the table, drawing a line to separate the rows from
    nothing.

    A table without a measurement column is left alone rather than
    special-cased downstream, which is what keeps `panel` free of the
    question.
    """
    if "measurement" not in columns:
        return [(row, row.cells, False) for row in rows]

    at = columns.index("measurement")
    laid: list[tuple[LatestRow, tuple[str, ...], bool]] = []
    for position, row in enumerate(rows):
        name = row.cells[at]
        repeated = position > 0 and rows[position - 1].cells[at] == name
        following = rows[position + 1].cells[at] if position + 1 < len(rows) else name
        laid.append(
            (
                row,
                (*row.cells[:at], "" if repeated else name, *row.cells[at + 1 :]),
                following != name,
            )
        )
    return laid


@final
class Stat(Static):
    """One tile: a heading, a large number, a unit and an age.

    In the spirit of a Grafana stat panel. `Digits` is what makes the
    value readable from across a room without a font dependency — the
    whole reason a tile beats a table row.
    """

    def __init__(self, tile: Tile) -> None:
        super().__init__()
        self._tile: Tile = tile

    @override
    def compose(self) -> ComposeResult:
        yield Label(self._tile.heading, classes="tile-heading")
        yield Digits(self._tile.reading or "—", classes="tile-value")
        yield Label(self._tile.unit, classes="tile-unit")
        yield Label(self._footer(), classes="tile-footer")

    def _footer(self) -> str:
        """The line under the number: what it is, and how it is doing.

        An alarm displaces the measurement rather than joining it. All
        three together overrun the width of a tile and get truncated,
        and of the three the measurement is the one already implied by
        the heading somebody chose.
        """
        if self._tile.alarm is not None:
            return f"{self._tile.alarm} · {self._tile.age}"
        if not self._tile.measurement:
            # Nothing was ever heard from this sensor, so there is no
            # measurement to name — the age carries the whole story.
            return self._tile.age
        return f"{self._tile.measurement} · {self._tile.age}"

    def on_mount(self) -> None:
        # An alarm outranks staleness: a reading that is out of range is
        # a fact about the experiment, and one that is merely old is a
        # fact about the network.
        if self._tile.alarm is not None:
            _ = self.add_class("alarm")
        elif not self._tile.found:
            _ = self.add_class("missing")
        elif self._tile.state is not Freshness.FRESH:
            _ = self.add_class(self._tile.state.value)


def keys_table(bindings: Sequence[BindingType]) -> RenderableType:
    """The panel's own keys, as something Rich can draw.

    Built from the bindings themselves so the two cannot drift, and
    listing only the panel's own. Textual's built-in key panel is
    reachable from the menu and shows the framework's bindings too —
    `Focus Next`, `Scroll Left` — which are true, and are noise on a
    view with one scrolling container and nothing to tab between.
    """
    table = Table(
        box=None,
        show_header=False,
        pad_edge=False,
        title=Text("keys", style="bold"),
        title_justify="left",
    )
    table.add_column(justify="right", style="bold")
    table.add_column()
    for entry in bindings:
        if isinstance(entry, Binding):
            binding = entry
        else:
            # A binding may also be written as a bare tuple, with the
            # description optional. Unpacked rather than splatted: the
            # two tuple shapes have different arities, so `Binding(*entry)`
            # cannot be checked against either constructor.
            key, action, *described = entry
            binding = Binding(key, action, described[0] if described else "")
        table.add_row(
            binding.key_display or binding.key,
            binding.tooltip or binding.description,
        )
    return table


@final
class Keys(ModalScreen[None]):
    """What the keys do, over the panel rather than instead of it.

    Its own screen rather than Textual's help panel, which has no
    binding to close it: opened from the menu it can only be dismissed
    from the menu, and somebody who does not know that has to quit the
    application to get their panel back. This one closes on the key
    that opened it, on escape, and on `q`.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,question_mark,q", "dismiss", "Close", key_display="esc"),
    ]

    CSS = """
    Keys {
        align: center middle;
        background: $background 60%;
    }
    #keys {
        border: round $panel-lighten-2;
        background: $surface;
        padding: 1 2;
        width: auto;
        height: auto;
    }
    """

    @override
    def compose(self) -> ComposeResult:
        yield Static(keys_table(Panel.BINDINGS), id="keys")


# What the rate menu is called. It rides in the border, which draws it
# as `╭─ title ─╮` and cuts it short when the box is narrower than that
# — so the menu has to be at least this wide plus the two characters the
# border spends on either side of the text. Measured, not guessed: at
# one character less the heading reads `refresh eve…`.
RATE_TITLE = "refresh every"
RATE_TITLE_BORDER = 2


@final
class Rate(ModalScreen[float | None]):
    """The cadences on offer, over the panel rather than instead of it.

    A menu rather than a key that steps through the same list. Stepping
    hides where in the list you are and how far there is left to go, so
    a rate three presses away is three guesses; shown all at once it is
    one keystroke and no guessing.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q", "dismiss", "Close", key_display="esc"),
    ]

    CSS = """
    Rate {
        align: center middle;
        background: $background 60%;
    }
    #rates {
        border: round $panel-lighten-2;
        border-title-align: center;
        background: $surface;
        padding: 0 1;
        width: auto;
        height: auto;
    }
    """

    def __init__(self, rates: Sequence[float], current: float) -> None:
        super().__init__()
        self._rates: tuple[float, ...] = tuple(rates)
        self._current: float = current

    @override
    def compose(self) -> ComposeResult:
        # The heading rides in the border rather than sitting above as a
        # label. A label and a list are two auto-width children of an
        # auto-width box, which Textual cannot centre against each other
        # without one of them being told a width in advance.
        #
        # Each rate is right-aligned so `1s` and `10s` line up on their
        # digits, then that block is centred in a cell wide enough for
        # the heading — otherwise the border sizes to `60s` and truncates
        # its own title to an ellipsis.
        digits = max(len(seconds(rate)) for rate in self._rates)
        cell = max(len(RATE_TITLE) + RATE_TITLE_BORDER, digits)
        yield OptionList(
            *(
                Option(seconds(rate).rjust(digits).center(cell), id=repr(rate))
                for rate in self._rates
            ),
            id="rates",
        )

    def on_mount(self) -> None:
        chosen = self.query_one("#rates", OptionList)
        chosen.border_title = RATE_TITLE
        # Opens on the rate in force, so the menu says what the panel is
        # doing as well as what it could do.
        chosen.highlighted = self._rates.index(self._current)
        _ = chosen.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        _ = self.dismiss(self._rates[event.option_index])


@final
class Panel(App[None]):
    """A table of current values, redrawn on a fixed cadence.

    The query runs in a worker thread. It blocks for tens of
    milliseconds, which is long enough to make a keypress feel sticky if
    it ran on the event loop, and there is no reason for `q` to wait for
    a database.
    """

    CSS = """
    #table {
        width: auto;
        height: auto;
    }
    #tiles {
        grid-size: 3;
        grid-gutter: 1 2;
        /* Sized explicitly. A grid whose columns are `auto` collapses
           its children to zero width, and a tile nobody can see is
           worse than a table. */
        grid-columns: 30;
        grid-rows: 8;
        width: auto;
        height: auto;
        padding: 1 0;
    }
    Stat {
        border: round $panel-lighten-2;
        padding: 0 1;
        content-align: center middle;
    }
    Stat.ageing { border: round $warning; }
    Stat.stale  { border: round $error; }
    Stat.missing { border: round $error; opacity: 0.6; }
    Stat.alarm  { border: heavy $error; }
    .tile-heading { width: 100%; text-align: center; text-style: bold; }
    /* `Digits` defaults to text-align: left, which puts the one thing
       the tile exists for against its left edge. */
    .tile-value { width: 100%; text-align: center; }
    .tile-unit { width: 100%; text-align: center; color: $text-muted; }
    .tile-footer { width: 100%; text-align: center; color: $text-muted; }
    Stat.alarm .tile-footer { color: $error; text-style: bold; }
    #caption { width: 100%; text-align: center; color: $text-muted; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit", tooltip="Close the panel."),
        Binding(
            "r",
            "choose_rate",
            "Change refresh rate",
            tooltip="Choose how often the panel redraws.",
        ),
        Binding(
            "m",
            "command_palette",
            "Menu",
            tooltip="Textual's command palette: themes, and everything else.",
        ),
        Binding(
            "question_mark",
            "keys",
            "Keys",
            key_display="?",
            tooltip="Show this list.",
        ),
    ]

    def __init__(
        self,
        *,
        measurements: list[str] | None,
        sensor_ids: list[str] | None,
        window: str,
        refresh: float,
        panels: tuple[PanelSpec, ...] = (),
        display: tuple[DisplaySpec, ...] = (),
        tz: tzinfo = UTC,
    ) -> None:
        super().__init__()
        self._measurements: list[str] | None = measurements
        self._sensor_ids: list[str] | None = sensor_ids
        self._window: str = window
        self._refresh: float = refresh
        # The ladder `r` walks, fixed at startup so the configured
        # interval stays on it however many times the key is pressed.
        self._cadences: tuple[float, ...] = cadences(refresh)
        self._tick: Timer | None = None
        self._panels: tuple[PanelSpec, ...] = panels
        # Named `_rules`, not `_display`: `App._display` is Textual's
        # own and overriding it with a tuple breaks the framework.
        self._rules: tuple[DisplaySpec, ...] = display
        self._tz: tzinfo = tz
        # The widest each column has had to be, so the table stops
        # changing size under a reading that gained a digit.
        self._widths: dict[str, int] = {}
        # The last snapshot, kept so a failed tick can leave the previous
        # table on screen rather than blanking it. Public because it is
        # what the tests assert against.
        self.latest: Snapshot = Snapshot(taken=datetime.now(UTC))

    @property
    def refresh_rate(self) -> float:
        """The cadence the panel is currently redrawing at.

        Read-only, and named apart from Textual's own `refresh`, which
        is a method that repaints a widget.
        """
        return self._refresh

    @override
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        if self._panels:
            # A layout is a set of things to watch, so it scrolls rather
            # than being squeezed: a tile shrunk to fit is a number
            # nobody can read from across the room.
            with VerticalScroll():
                with Center():
                    yield Grid(id="tiles")
                yield Label(id="caption")
        else:
            with Middle(), Center():
                yield Static(id="table")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = THEME
        self.title = "labmon"
        self.sub_title = self._window
        self._schedule()
        self.refresh_now()

    def _schedule(self) -> None:
        """Restart the tick at the current cadence.

        The old timer is stopped first. Leaving it running would leave
        two of them refreshing at two different rates, and the panel
        would settle on the faster one no matter what the status line
        claimed.
        """
        if self._tick is not None:
            self._tick.stop()
        self._tick = self.set_interval(self._refresh, self.refresh_now)

    def action_choose_rate(self) -> None:
        """`r`, for the menu of cadences the panel can redraw at."""
        _ = self.push_screen(Rate(self._cadences, self._refresh), self._set_rate)

    def _set_rate(self, chosen: float | None) -> None:
        """Adopt the cadence picked from the menu, if one was.

        Redraws immediately rather than waiting for the next tick: the
        status line carries the interval, and a menu that appears to do
        nothing for the next thirty seconds is one somebody opens again.
        Nothing is re-queried — a cadence is not a reading.
        """
        if chosen is None or chosen == self._refresh:
            return
        self._refresh = chosen
        self._schedule()
        self._draw()
        self.notify(f"refreshing every {seconds(chosen)}", timeout=2)

    def action_keys(self) -> None:
        """`?`, for the list of what the other keys do."""
        _ = self.push_screen(Keys())

    def refresh_now(self) -> None:
        _ = self._query()

    @work(thread=True, exclusive=True)
    def _query(self) -> None:
        snapshot = take(
            self._measurements,
            self._sensor_ids,
            self._window,
            display=self._rules,
        )
        self.call_from_thread(self._show, snapshot)

    def _show(self, snapshot: Snapshot) -> None:
        # A failed tick keeps the previous rows: a blank panel says less
        # than a stale one labelled as stale, and the caption says so.
        if snapshot.error is None or not self.latest.rows:
            self.latest = snapshot
        else:
            self.latest = Snapshot(
                taken=snapshot.taken,
                columns=self.latest.columns,
                rows=self.latest.rows,
                quiet=self.latest.quiet,
                error=snapshot.error,
            )
        self._draw()

    def _draw(self) -> None:
        """Redraw from the snapshot already held.

        Separate from `_show` because a cadence change alters what the
        status line says without altering a single reading, and there is
        no sense querying a database to report a keystroke.
        """
        if self._panels:
            self._draw_tiles()
            return
        self._widths = stretched(self._widths, self.latest)
        self.query_one("#table", Static).update(
            panel(
                self.latest,
                window=self._window,
                refresh=self._refresh,
                tz=self._tz,
                widths=self._widths,
            )
        )

    def _draw_tiles(self) -> None:
        """Replace the grid's contents with this tick's tiles.

        Rebuilt rather than updated in place. A tile's border and its
        classes depend on the reading, a layout is a handful of widgets
        rather than a table of thousands, and rebuilding cannot leave a
        stale class behind on a tile that has recovered.
        """
        grid = self.query_one("#tiles", Grid)
        _ = grid.remove_children()
        _ = grid.mount_all(
            Stat(tile) for tile in tiles(self.latest, self._panels, self._rules)
        )
        self.query_one("#caption", Label).update(
            status(
                self.latest,
                window=self._window,
                refresh=self._refresh,
                tz=self._tz,
            )
        )
