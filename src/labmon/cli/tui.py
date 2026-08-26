"""The Textual application `labmon monitor` runs.

Thin on purpose. Everything that decides *what* to show lives in
`labmon.cli.monitor`, which needs no event loop and is tested without
one; this module places it on a screen and arranges for it to happen
again in two seconds.

Imported only from inside `labmon.cli.commands.monitor`, because Textual
lives behind the `tui` extra and pulls Rich and a tree of its own
dependencies. Nothing that merely writes readings should pay for that.
"""

from datetime import UTC, datetime
from typing import ClassVar, final, override

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.widgets import Footer, Header, Static

from labmon.cli.monitor import Snapshot, status, take


@final
class Panel(App[None]):
    """A table of current values, redrawn on a fixed cadence.

    The query runs in a worker thread. It blocks for tens of
    milliseconds, which is long enough to make a keypress feel sticky if
    it ran on the event loop, and there is no reason for `q` to wait for
    a database.
    """

    CSS = """
    Static { padding: 1 2; }
    #status { color: $text-muted; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        ("q", "quit", "Quit"),
        ("r", "refresh_now", "Refresh"),
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
        self.latest: Snapshot = Snapshot(body="", taken=datetime.now(UTC))

    @override
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="table")
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
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
        # A failed tick keeps the previous table: a blank panel says
        # less than a stale one labelled as stale.
        if snapshot.error is None or not self.latest.body:
            self.latest = snapshot
        else:
            self.latest = Snapshot(
                body=self.latest.body, taken=snapshot.taken, error=snapshot.error
            )
        self.query_one("#table", Static).update(Text.from_ansi(self.latest.body))
        self.query_one("#status", Static).update(
            status(self.latest, window=self._window, refresh=self._refresh)
        )
