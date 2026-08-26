"""The Textual application `labmon monitor` runs.

Thin on purpose. Everything that decides *what* to show lives in
`labmon.cli.monitor` and `labmon.cli.render`, neither of which needs an
event loop; this module gives those rows a border, a colour scheme and a
reason to happen again in two seconds.

Imported only from inside `labmon.cli.commands.monitor`, because Textual
lives behind the `tui` extra and pulls Rich and a tree of its own
dependencies. Nothing that merely writes readings should pay for that.
"""

from datetime import UTC, datetime
from typing import ClassVar, final, override

from rich import box
from rich.align import Align
from rich.console import RenderableType
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.containers import Center, Middle
from textual.widgets import Footer, Header, Static

from labmon.cli.age import Freshness
from labmon.cli.monitor import Snapshot, status, take

# The panel's default look. Nord is calm at a glance and legible on a
# projector, which is where a panel beside an experiment tends to end
# up. It is a default, not a decision: Textual ships twenty themes and
# the command palette switches between them live.
THEME = "nord"

# Columns holding a number, right-aligned so digits line up under each
# other. A column of left-aligned floats has to be read digit by digit
# to compare two of them.
_NUMERIC: frozenset[str] = frozenset({"value", "mean", "sd", "n"})

# What each column is called on screen. The internal names are the
# database's; a panel has room to spell them.
_HEADINGS: dict[str, str] = {
    "measurement": "measurement",
    "sensor_id": "sensor",
    "value": "value",
    "unit": "unit",
    "age": "age",
    "mean": "mean",
    "sd": "sd",
    "n": "n",
}

# How a row is tinted by how long ago it last reported. Fresh rows are
# left alone: colouring everything colours nothing.
_STATE_STYLE: dict[Freshness, str] = {
    Freshness.FRESH: "",
    Freshness.AGEING: "yellow",
    Freshness.STALE: "red",
}


def panel(snapshot: Snapshot, *, window: str, refresh: float) -> RenderableType:
    """One tick, as something Rich can draw.

    Separate from the widget so it can be rendered to text and asserted
    against without starting an application.
    """
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
        caption=Text(status(snapshot, window=window, refresh=refresh), style="dim"),
        expand=False,
    )
    for name in snapshot.columns:
        heading = _HEADINGS.get(name, name)
        if name in _NUMERIC:
            # Right-aligned so digits line up under each other, and
            # never wrapped: a reading split across two lines is a
            # reading nobody can compare against the one above it.
            table.add_column(heading, justify="right", no_wrap=True)
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
            )

    for row in snapshot.rows:
        table.add_row(*row.cells, style=_STATE_STYLE[row.state])

    return Align.center(table)


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
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        ("q", "quit", "Quit"),
        ("r", "refresh_now", "Refresh"),
        ("p", "command_palette", "Theme"),
    ]

    def __init__(
        self,
        *,
        measurements: list[str] | None,
        sensor_ids: list[str] | None,
        window: str,
        refresh: float,
    ) -> None:
        super().__init__()
        self._measurements: list[str] | None = measurements
        self._sensor_ids: list[str] | None = sensor_ids
        self._window: str = window
        self._refresh: float = refresh
        # The last snapshot, kept so a failed tick can leave the previous
        # table on screen rather than blanking it. Public because it is
        # what the tests assert against.
        self.latest: Snapshot = Snapshot(taken=datetime.now(UTC))

    @override
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Middle(), Center():
            yield Static(id="table")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = THEME
        self.title = "labmon"
        self.sub_title = self._window
        _ = self.set_interval(self._refresh, self.refresh_now)
        self.refresh_now()

    def action_refresh_now(self) -> None:
        """`r`, for somebody who does not want to wait for the cadence."""
        self.refresh_now()

    def refresh_now(self) -> None:
        _ = self._query()

    @work(thread=True, exclusive=True)
    def _query(self) -> None:
        snapshot = take(self._measurements, self._sensor_ids, self._window)
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
        self.query_one("#table", Static).update(
            panel(self.latest, window=self._window, refresh=self._refresh)
        )
