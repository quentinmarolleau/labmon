"""`labmon monitor` — the panel, and the snapshot behind each tick."""

import asyncio
import io
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast, override

import pyarrow as pa
import pytest
from textual.widgets import OptionList

from labmon.cli import monitor
from labmon.cli.age import Freshness
from labmon.cli.main import build_app
from labmon.config import Display, Panel
from tests.cli_runner import Invocation, invoke

_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def _sensors(snapshot: "monitor.Snapshot") -> list[str]:
    """The sensor column of every row, in the order they are drawn."""
    at = snapshot.columns.index("sensor_id")
    return [row.cells[at] for row in snapshot.rows]


def _run(*args: str) -> Invocation:
    return invoke(build_app(), list(args))


class PanelClient:
    """Two sensors, one fresh and one that stopped, with statistics."""

    def query(
        self,
        query: str,
        language: str = "sql",
        mode: str = "all",
        database: str | None = None,
        **kwargs: object,
    ) -> pa.Table:
        _ = (language, mode, database, kwargs)
        if "SHOW TABLES" in query:
            return pa.table(
                {
                    "table_schema": pa.array(["iox"]),
                    "table_name": pa.array(["temperature"]),
                }
            )
        if "information_schema.columns" in query:
            return pa.table(
                {"column_name": pa.array(["time", "value", "sensor_id", "unit"])}
            )
        now = datetime.now(UTC)
        return pa.table(
            {
                "measurement": pa.array(["temperature", "temperature"]),
                "sensor_id": pa.array(["cryo-77k", "abandoned"]),
                "time": pa.array(
                    [now - timedelta(seconds=2), now - timedelta(hours=3)],
                    pa.timestamp("ms", tz="UTC"),
                ),
                "value": pa.array([77.01, 4.2]),
                "unit": pa.array(["K", "K"]),
                "mean": pa.array([76.98, 4.19]),
                "sd": pa.array([0.031, 0.004]),
                "n": pa.array([1800, 12], pa.int64()),
            }
        )

    def close(self) -> None:
        return None


# --------------------------------------------------------------------------
# One tick's worth of text
# --------------------------------------------------------------------------


def test_a_snapshot_carries_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    taken = monitor.take(None, None, "15m", now=_NOW)

    assert _sensors(taken) == ["abandoned", "cryo-77k"]
    assert taken.error is None


def test_a_snapshot_asks_for_statistics(monkeypatch: pytest.MonkeyPatch) -> None:
    # The panel's whole point over `query latest` is showing how the
    # window behaved, not only where it ended.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    taken = monitor.take(None, None, "15m", now=_NOW)

    assert "mean" in taken.columns
    assert "sd" in taken.columns


def test_a_failed_tick_becomes_a_message_rather_than_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A panel that dies on one network blip is useless. Re-querying the
    # window is self-healing: the next tick is simply correct.
    def refuse() -> object:
        raise OSError("no route to host")

    monkeypatch.setattr("labmon.influx.get_client", refuse)

    taken = monitor.take(None, None, "15m", now=_NOW)

    assert taken.error is not None
    assert "no route to host" in taken.error


def test_a_snapshot_says_when_it_was_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    taken = monitor.take(None, None, "15m", now=_NOW)

    assert taken.taken == _NOW


def test_the_status_line_names_the_window_and_the_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    line = monitor.status(
        monitor.take(None, None, "15m", now=_NOW), window="15m", refresh=2.0
    )

    assert "15m" in line
    assert "every 2s" in line
    assert "2 sensors" in line


def test_the_status_line_clock_follows_the_readers_zone() -> None:
    # Textual's own header clock shows local time three lines above this
    # one, so a UTC clock here puts two clocks on one screen disagreeing
    # by whole hours — and #170 exists so terminal output follows the
    # person rather than the storage.
    from zoneinfo import ZoneInfo

    taken = datetime(2026, 8, 28, 22, 30, 15, tzinfo=UTC)

    line = monitor.status(
        monitor.Snapshot(taken=taken),
        window="15m",
        refresh=2.0,
        tz=ZoneInfo("Asia/Tokyo"),
    )

    assert line.endswith("07:30:15")


def test_the_status_line_clock_defaults_to_utc() -> None:
    # Nothing configured is the overwhelmingly common case, and it has to
    # stay the behaviour it always was.
    taken = datetime(2026, 8, 28, 22, 30, 15, tzinfo=UTC)

    line = monitor.status(monitor.Snapshot(taken=taken), window="15m", refresh=2.0)

    assert line.endswith("22:30:15")


def test_the_status_line_leads_with_the_failure_when_there_is_one() -> None:
    line = monitor.status(
        monitor.Snapshot(taken=_NOW, error="database unreachable"),
        window="15m",
        refresh=2.0,
    )

    assert line.startswith("database unreachable")


# --------------------------------------------------------------------------
# The panel itself
# --------------------------------------------------------------------------


def _drive(scenario: Callable[[], object]) -> None:
    """Run one async Textual scenario from a synchronous test."""
    asyncio.run(scenario())  # pyright: ignore[reportArgumentType]


def test_the_panel_draws_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=2.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert _sensors(app.latest) == ["abandoned", "cryo-77k"]

    _drive(scenario)


def test_q_quits_the_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=2.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            assert not app.is_running

    _drive(scenario)


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------


def test_monitor_refuses_a_refresh_of_zero() -> None:
    result = _run("monitor", "--refresh", "0s")

    assert result.exit_code != 0


def test_monitor_reports_a_missing_extra_rather_than_an_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Textual lives behind `labmon[tui]`, and an ImportError traceback
    # names a module rather than the install command that fixes it.
    # `None` in sys.modules is what an absent module looks like to an
    # import statement, without disturbing anything else.
    import sys

    monkeypatch.setitem(sys.modules, "textual", None)
    monkeypatch.delitem(sys.modules, "labmon.cli.tui", raising=False)

    result = _run("monitor")

    assert result.exit_code != 0
    assert "labmon[tui]" in result.output


def test_monitor_refuses_a_refresh_that_is_not_a_duration() -> None:
    result = _run("monitor", "--refresh", "soon")

    assert result.exit_code != 0


def test_monitor_refuses_a_window_it_cannot_read() -> None:
    # Checked before the screen is cleared. A mistake that surfaced on
    # the first tick would have already taken over the terminal.
    result = _run("monitor", "--since", "last tuesday")

    assert result.exit_code != 0


def test_the_flags_reach_the_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[dict[str, object]] = []

    class Recording:
        def __init__(self, **kwargs: object) -> None:
            built.append(kwargs)

        def run(self) -> None:
            return None

    monkeypatch.setattr("labmon.cli.tui.Panel", Recording)

    result = _run(
        "monitor",
        "--since",
        "1h",
        "--refresh",
        "5s",
        "--measurement",
        "temperature",
        "--sensor-id",
        "cryo-77k",
    )

    assert result.exit_code == 0, result.output
    assert built == [
        {
            "measurements": ["temperature"],
            "sensor_ids": ["cryo-77k"],
            "window": "1h",
            "refresh": 5.0,
            "panels": (),
            "display": (),
            "tz": UTC,
        }
    ]


def test_the_configuration_supplies_the_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "config" / "labmon" / "labmon.toml"
    config.parent.mkdir(parents=True)
    _ = config.write_text('[monitor]\nrefresh = "10s"\nwindow = "6h"\n')

    built: list[dict[str, object]] = []

    class Recording:
        def __init__(self, **kwargs: object) -> None:
            built.append(kwargs)

        def run(self) -> None:
            return None

    monkeypatch.setattr("labmon.cli.tui.Panel", Recording)

    result = _run("monitor")

    assert result.exit_code == 0, result.output
    assert built[0]["refresh"] == 10.0
    assert built[0]["window"] == "6h"


def _prompts(offered: OptionList) -> list[str]:
    """Every choice the menu shows, in the order it shows them.

    Stripped of the padding that centres each rate under the heading.
    """
    return [
        str(offered.get_option_at_index(index).prompt).strip()
        for index in range(offered.option_count)
    ]


def test_r_opens_the_rate_menu_on_the_rate_in_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The panel refreshes itself, so a key that forces one early buys
    # nothing. Changing how often it happens is the thing somebody
    # standing at the bench actually wants.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel, Rate

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=10.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()

            assert isinstance(app.screen, Rate)
            offered = app.screen.query_one(OptionList)
            assert _prompts(offered) == [
                "1s",
                "2s",
                "5s",
                "10s",
                "30s",
                "60s",
            ]
            # Opened on the cadence the panel is running at, so the menu
            # says what it is doing as well as what it could do.
            assert offered.highlighted == 3

    _drive(scenario)


def test_choosing_a_rate_from_the_menu_adopts_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel, Rate

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=2.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            await pilot.press("down", "down", "enter")
            await pilot.pause()

            assert app.refresh_rate == 10.0
            assert not isinstance(app.screen, Rate)

    _drive(scenario)


def test_leaving_the_rate_menu_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel, Rate

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=2.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

            assert app.refresh_rate == 2.0
            assert not isinstance(app.screen, Rate)
            assert app.is_running

    _drive(scenario)


def test_the_configured_rate_is_on_the_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A cadence nobody would have picked from a list of round numbers is
    # still the one written in the file, so it has to be offered.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=3.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()

            assert "3s" in _prompts(app.screen.query_one(OptionList))

    _drive(scenario)


def test_changing_the_rate_says_so_without_re_querying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A cadence is not a reading. Re-querying to redraw a status line
    # would put a database round trip behind a keystroke for nothing.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=2.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            taken = app.latest.taken

            calls: list[object] = []

            def counted(*args: object, **kwargs: object) -> monitor.Snapshot:
                # Recorded rather than raised: this runs on a worker
                # thread, where an exception would be logged and the
                # test would pass regardless.
                calls.append((args, kwargs))
                return monitor.Snapshot(taken=datetime.now(UTC))

            monkeypatch.setattr("labmon.cli.tui.take", counted)
            await pilot.press("r")
            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.pause()

            assert not calls
            assert app.refresh_rate == 5.0
            assert app.latest.taken == taken
            assert "every 5s" in monitor.status(
                app.latest, window="15m", refresh=app.refresh_rate
            )

    _drive(scenario)


def test_the_theme_menu_opens_on_the_theme_in_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel, Themes

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=3600.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.search_themes()
            await pilot.pause()

            assert isinstance(app.screen, Themes)
            offered = app.screen.query_one(OptionList)
            assert _prompts(offered) == sorted(app.available_themes)
            assert _prompts(offered)[offered.highlighted or 0] == app.theme

    _drive(scenario)


def test_moving_over_a_theme_applies_it_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A theme is picked by looking at it, not by reading its name. The
    # menu is the preview: the panel behind it is already drawn in the
    # theme under the cursor, against the readings actually on screen.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=3600.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.search_themes()
            await pilot.pause()
            offered = app.screen.query_one(OptionList)
            await pilot.press("down")
            await pilot.pause()

            assert app.theme == _prompts(offered)[offered.highlighted or 0]
            assert app.theme != "nord"

    _drive(scenario)


def test_choosing_a_theme_keeps_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel, Themes

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=3600.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.search_themes()
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            previewed = app.theme
            await pilot.press("enter")
            await pilot.pause()

            assert app.theme == previewed
            assert not isinstance(app.screen, Themes)

    _drive(scenario)


def test_leaving_the_theme_menu_puts_the_theme_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A preview that stuck because somebody pressed escape would be a
    # menu that changes the panel by being opened.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel, Themes

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=3600.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            was = app.theme
            app.search_themes()
            await pilot.pause()
            await pilot.press("down", "down")
            await pilot.pause()
            assert app.theme != was

            await pilot.press("escape")
            await pilot.pause()

            assert app.theme == was
            assert not isinstance(app.screen, Themes)

    _drive(scenario)


def test_the_command_palette_reaches_the_previewing_theme_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The menu is Textual's own, opened by its own system command. The
    # preview has to be on that path, not only on a key of our own.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=3600.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_change_theme()
            await pilot.pause()

            from labmon.cli.tui import Themes

            assert isinstance(app.screen, Themes)

    _drive(scenario)


def test_a_failed_refresh_keeps_the_last_good_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A blank panel says less than a stale one labelled as stale, and
    # the label is on the status line right below it.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=3600.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert _sensors(app.latest) == ["abandoned", "cryo-77k"]

            def fail(*args: object, **kwargs: object) -> monitor.Snapshot:
                _ = (args, kwargs)
                return monitor.Snapshot(taken=datetime.now(UTC), error="unreachable")

            monkeypatch.setattr("labmon.cli.tui.take", fail)
            app.refresh_now()
            await pilot.pause()
            await pilot.pause()

            assert app.latest.error == "unreachable"
            assert _sensors(app.latest) == ["abandoned", "cryo-77k"]

    _drive(scenario)


def test_the_first_refresh_failing_leaves_nothing_to_keep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Nothing good has been drawn yet, so the failure is all there is.
    def refuse() -> object:
        raise OSError("no route to host")

    monkeypatch.setattr("labmon.influx.get_client", refuse)
    from labmon.cli.tui import Panel

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=3600.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            assert app.latest.error is not None
            assert app.latest.rows == ()

    _drive(scenario)


def test_an_import_failure_that_is_not_a_missing_extra_is_not_disguised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Textual is installed and the panel still failed to import: a
    # renamed symbol after an upgrade, say. Reporting that as "install
    # the extra" sends somebody to reinstall a package they already
    # have, and hides the traceback that would have said what broke.
    import sys

    monkeypatch.delitem(sys.modules, "labmon.cli.tui", raising=False)

    real = __import__

    def half_broken(name: str, *args: object, **kwargs: object) -> object:
        if name == "labmon.cli.tui":
            raise ImportError("cannot import name 'Binding' from 'textual'")
        return cast(object, real(name, *args, **kwargs))  # pyright: ignore[reportArgumentType]

    import builtins

    monkeypatch.setattr(builtins, "__import__", half_broken)

    result = _run("monitor")

    assert result.exit_code != 0
    assert "labmon[tui]" not in result.output


def _unboxed(text: str) -> str:
    """`text` with Rich's box and every space taken out of it.

    Typer renders a `BadParameter` inside a Rich panel wrapped to the
    terminal width, so an interpreter path long enough to wrap arrives
    split across two lines with box rules and padding in the middle of
    it. A checkout under a deep directory, a CI runner or a container
    all reach that width; this one happens not to, which is the whole
    reason the plain substring check survived being written.

    Whitespace goes rather than just newlines, since the wrap leaves
    padding either side of the rule. Both sides of the comparison are
    put through it, so a path that legitimately holds a space still
    matches.
    """
    return "".join(text.split()).replace("\u2502", "").replace("\u2500", "")


def test_the_missing_extra_message_names_the_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An editable checkout installed with `uv tool install` has its own
    # environment, and `uv sync` does not touch it — so "not installed"
    # is only half the answer without saying where.
    import sys

    monkeypatch.setitem(sys.modules, "textual", None)
    monkeypatch.delitem(sys.modules, "labmon.cli.tui", raising=False)

    result = _run("monitor")

    assert _unboxed(sys.executable) in _unboxed(result.output)


# --------------------------------------------------------------------------
# What the panel draws
# --------------------------------------------------------------------------


def _drawn(
    snapshot: "monitor.Snapshot",
    width: int = 100,
    widths: "dict[str, int] | None" = None,
    source: str = "",
) -> str:
    """The panel rendered to plain text, without starting an app."""

    from labmon.cli.tui import panel

    return _to_text(
        panel(snapshot, window="15m", refresh=2.0, widths=widths, source=source),
        width=width,
    )


def _to_text(renderable: object, width: int) -> str:
    """Anything Rich can draw, as the plain text it draws to."""
    from rich.console import Console, RenderableType

    console = Console(width=width, record=True, file=io.StringIO())
    console.print(cast("RenderableType", renderable))
    return console.export_text()


def test_the_panel_has_a_border_and_a_caption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    drawn = _drawn(monitor.take(None, None, "15m", now=_NOW))

    assert "─" in drawn
    assert "cryo-77k" in drawn
    assert "2 sensors" in drawn
    assert "every 2s" in drawn


def test_the_panel_names_the_sensor_column_something_shorter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A panel has room to spell a column; it does not have to use the
    # database's name for it.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    drawn = _drawn(monitor.take(None, None, "15m", now=_NOW))

    assert "sensor_id" not in drawn
    assert "sensor" in drawn


def _snapshot(table: "pa.Table") -> "monitor.Snapshot":
    """A snapshot of exactly these readings, without a database."""
    from labmon.cli.render import latest_rows

    columns, rows = latest_rows(table, _NOW)
    return monitor.Snapshot(taken=_NOW, columns=columns, rows=tuple(rows))


def _two_measurements() -> "monitor.Snapshot":
    """A pressure and two temperatures, as the panel orders them."""
    return _snapshot(
        pa.table(
            {
                "measurement": pa.array(["pressure", "temperature", "temperature"]),
                "sensor_id": pa.array(["chamber-1", "cryo-4k", "cryo-77k"]),
                "time": pa.array([_NOW] * 3, pa.timestamp("ms", tz="UTC")),
                "value": pa.array([1.8e-07, 4.301, 77.01]),
                "unit": pa.array(["mbar", "K", "K"]),
            }
        )
    )


def test_a_measurement_is_written_once_for_the_rows_it_covers() -> None:
    # The rows are sorted by measurement, so the column repeats a word
    # the row above has already said — width spent on nothing.
    drawn = _drawn(_two_measurements())

    assert drawn.count("temperature") == 1
    assert drawn.count("pressure") == 1


def test_a_rule_separates_one_measurement_from_the_next() -> None:
    # Blanking the repeats alone would leave two groups touching, with
    # nothing to say where one stops. The rule is what makes the
    # grouping readable rather than merely shorter.
    drawn = _drawn(_two_measurements())

    ruled = [line for line in drawn.splitlines() if "\u251c" in line]
    assert len(ruled) == 2


def test_a_table_with_no_measurement_column_is_drawn_as_it_comes() -> None:
    # A column is shown when the result carries it, so a measurement-less
    # table is a shape the panel has to be able to draw — with nothing to
    # group by, and nothing to rule off.
    drawn = _drawn(
        _snapshot(
            pa.table(
                {
                    "sensor_id": pa.array(["cryo-77k"]),
                    "time": pa.array([_NOW], pa.timestamp("ms", tz="UTC")),
                    "value": pa.array([77.01]),
                    "unit": pa.array(["K"]),
                }
            )
        )
    )

    assert "cryo-77k" in drawn
    assert len([line for line in drawn.splitlines() if "\u251c" in line]) == 1


def test_a_single_measurement_is_not_ruled_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A rule under the last group would sit against the bottom border.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    drawn = _drawn(monitor.take(None, None, "15m", now=_NOW))

    ruled = [line for line in drawn.splitlines() if "\u251c" in line]
    assert len(ruled) == 1


def test_nothing_to_show_says_so_rather_than_drawing_an_empty_box() -> None:
    drawn = _drawn(monitor.Snapshot(taken=_NOW))

    assert "no readings matched" in drawn
    assert "─" not in drawn


def test_a_failure_with_nothing_to_show_states_the_failure() -> None:
    drawn = _drawn(monitor.Snapshot(taken=_NOW, error="database unreachable"))

    assert "database unreachable" in drawn


@pytest.mark.parametrize("width", [70, 92, 100, 140])
def test_nothing_is_ever_ellipsised(
    monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    # A truncated reading is a wrong reading, and a truncated name is
    # worse than it looks: `wavemeter-1` and `wavemeter-thz` both become
    # `wavemet…`, so two rows become indistinguishable. Narrow terminals
    # fold names onto a second line instead.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    drawn = _drawn(monitor.take(None, None, "15m", now=_NOW), width=width)

    assert "…" not in drawn


def test_readings_are_shown_exactly_as_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A reading is recorded, not computed. Only the average and the
    # deviation are rounded, and only against each other — a sensor
    # whose full reading is too long to glance at is given a precision.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    taken = monitor.take(None, None, "15m", now=_NOW)
    values = [row.cells[taken.columns.index("value")] for row in taken.rows]

    assert "77.01" in values


def test_the_status_line_counts_the_sensors_that_have_gone_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A remembered sensor is on screen with a blank value; the count is
    # what says how many of the rows are in that state.
    from labmon.cli.roster import Known, cache_path, merge, save

    save(
        cache_path(),
        merge(
            {},
            [
                Known(
                    sensor_id="departed",
                    measurement="temperature",
                    unit="K",
                    last_seen=_NOW - timedelta(days=3),
                )
            ],
        ),
    )
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    line = monitor.status(
        monitor.take(None, None, "15m", now=_NOW), window="15m", refresh=2.0
    )

    assert "3 sensors, 1 quiet" in line


def _readings(
    value: float, mean: float = 77.0, sd: float = 0.031
) -> "monitor.Snapshot":
    """One sensor's row, with statistics, at whatever width the numbers take."""
    return _snapshot(
        pa.table(
            {
                "measurement": pa.array(["temperature"]),
                "sensor_id": pa.array(["cryo-77k"]),
                "time": pa.array([_NOW], pa.timestamp("ms", tz="UTC")),
                "value": pa.array([value]),
                "unit": pa.array(["K"]),
                "mean": pa.array([mean]),
                "sd": pa.array([sd]),
                "n": pa.array([1800], pa.int64()),
            }
        )
    )


def _box(drawn: str) -> int:
    """How wide the drawn table is, measured on its top border."""
    return max(len(line.strip()) for line in drawn.splitlines() if "\u256d" in line)


def test_a_column_is_never_narrowed_by_a_shorter_reading() -> None:
    # A column sized to whatever this tick happens to hold changes width
    # whenever a reading gains or loses a digit, and every column right
    # of it — and the centred table itself — shifts with it. On a panel
    # redrawing every two seconds that is the whole table twitching.
    from labmon.cli.tui import stretched

    wide = stretched({}, _readings(77.0123456))

    assert stretched(wide, _readings(4.2)) == wide


def test_a_column_is_widened_by_a_longer_reading() -> None:
    from labmon.cli.tui import stretched

    held = stretched(stretched({}, _readings(4.2)), _readings(77.0123456))

    assert held["value"] == len("77.0123456")


def test_a_column_is_at_least_as_wide_as_its_heading() -> None:
    # `measurement` is a longer word than most of what goes under it.
    from labmon.cli.tui import stretched

    assert stretched({}, _readings(4.2))["measurement"] == len("measurement")


def test_the_table_is_drawn_at_the_widths_it_is_given() -> None:
    from labmon.cli.tui import stretched

    held = stretched({}, _readings(77.0123456))
    wide = _drawn(_readings(77.0123456), widths=held)

    assert _box(_drawn(_readings(4.2), widths=held)) == _box(wide)


def test_the_table_holds_its_width_when_the_readings_shrink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The whole point of the running widths, at the level somebody sees
    # it: the box does not twitch when a digit goes away.
    from textual.widgets import Static

    from labmon.cli.tui import Panel

    coming = [_readings(77.0123456), _readings(4.2)]

    def next_tick(*args: object, **kwargs: object) -> "monitor.Snapshot":
        _ = (args, kwargs)
        return coming.pop(0) if len(coming) > 1 else coming[0]

    monkeypatch.setattr("labmon.cli.tui.take", next_tick)

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=3600.0)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            await pilot.pause()
            first = app.query_one("#table", Static).region.width

            app.refresh_now()
            await pilot.pause()
            await pilot.pause()

            assert app.latest.rows[0].cells[app.latest.columns.index("value")] == "4.2"
            assert app.query_one("#table", Static).region.width == first

    _drive(scenario)


def test_the_source_names_the_database_and_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INFLUXDB_DATABASE", "cryostat")
    monkeypatch.setenv("INFLUXDB_HOST", "http://kelvin:8181")

    assert monitor.source() == "cryostat @ http://kelvin:8181"


def test_the_source_falls_back_to_the_defaults_the_client_would_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A title naming a database the panel is not reading would be worse
    # than no title at all.
    monkeypatch.delenv("INFLUXDB_DATABASE", raising=False)
    monkeypatch.delenv("INFLUXDB_HOST", raising=False)

    from labmon.influx import influx_database, influx_host

    assert monitor.source() == f"{influx_database()} @ {influx_host()}"


def test_the_title_says_which_database_is_being_read() -> None:
    # A panel is left open for hours, and "labmon" alone cannot say
    # which of two stacks on the same machine it is watching — a demo
    # compose file beside the real one shows the same sensors either way.
    drawn = _drawn(_readings(77.01), source="cryostat @ http://kelvin:8181")

    assert "labmon" in drawn
    assert _unboxed("cryostat @ http://kelvin:8181") in _unboxed(drawn)


def test_the_title_is_just_labmon_when_there_is_nothing_to_add() -> None:
    drawn = _drawn(_readings(77.01))

    assert "labmon" in drawn
    assert "@" not in drawn


def test_the_panel_tells_the_table_which_database_it_is_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    monkeypatch.setenv("INFLUXDB_DATABASE", "cryostat")
    monkeypatch.setenv("INFLUXDB_HOST", "http://kelvin:8181")

    from labmon.cli import tui

    drawn: list[object] = []
    real = tui.panel

    def spy(*args: object, **kwargs: object) -> object:
        drawn.append(kwargs.get("source"))
        return real(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr("labmon.cli.tui.panel", spy)

    async def scenario() -> None:
        app = tui.Panel(
            measurements=None, sensor_ids=None, window="15m", refresh=3600.0
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()

            assert drawn == ["cryostat @ http://kelvin:8181"]

    _drive(scenario)


def test_the_table_is_centred_on_a_wide_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pinned to the left edge of a wide terminal, the table reads as an
    # accident. A margin either side reads as a panel.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from textual.widgets import Static

    from labmon.cli.tui import Panel

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=2.0)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            await pilot.pause()
            region = app.query_one("#table", Static).region
            assert region.x > 0
            assert region.x + region.width < app.size.width

    _drive(scenario)


# --------------------------------------------------------------------------
# Tiles
# --------------------------------------------------------------------------


def _taken(monkeypatch: pytest.MonkeyPatch) -> "monitor.Snapshot":
    # Real `now`, because PanelClient stamps its rows relative to the
    # real clock: pinning one side and not the other makes a sensor that
    # stopped three hours ago look like one reporting from the future.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    return monitor.take(None, None, "15m")


def test_a_tile_is_made_for_each_panel_in_the_order_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taken = _taken(monkeypatch)
    panels = (Panel(sensor_id="abandoned"), Panel(sensor_id="cryo-77k"))

    made = monitor.tiles(taken, panels)

    assert [tile.heading for tile in made] == ["abandoned", "cryo-77k"]


def test_a_title_replaces_the_sensor_name(monkeypatch: pytest.MonkeyPatch) -> None:
    taken = _taken(monkeypatch)

    made = monitor.tiles(taken, (Panel(sensor_id="cryo-77k", title="Cold finger"),))

    assert made[0].heading == "Cold finger"


def test_a_tile_always_says_which_measurement_it_settled_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `measurement` is optional in the file, so what is on screen must
    # not be ambiguous even when the configuration was.
    taken = _taken(monkeypatch)

    made = monitor.tiles(taken, (Panel(sensor_id="cryo-77k"),))

    assert made[0].measurement == "temperature"


def test_a_panel_naming_a_sensor_that_is_not_reporting_still_gets_a_tile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Silently dropping it is the failure this whole feature exists to
    # prevent: the tile you configured is the one you are watching for.
    taken = _taken(monkeypatch)

    made = monitor.tiles(taken, (Panel(sensor_id="nowhere"),))

    assert made[0].found is False
    assert made[0].reading == ""


def test_precision_wins_over_the_automatic_rounding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taken = _taken(monkeypatch)

    made = monitor.tiles(taken, (Panel(sensor_id="cryo-77k", precision=3),))

    assert made[0].reading == "77.010"


def test_a_format_forces_scientific(monkeypatch: pytest.MonkeyPatch) -> None:
    taken = _taken(monkeypatch)

    made = monitor.tiles(taken, (Panel(sensor_id="cryo-77k", format="scientific"),))

    assert "e+" in made[0].reading


def test_a_reading_above_the_threshold_raises_the_alarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taken = _taken(monkeypatch)

    made = monitor.tiles(taken, (Panel(sensor_id="cryo-77k", warn_above=70.0),))

    assert made[0].alarm is not None
    assert "70" in made[0].alarm


def test_a_reading_below_the_threshold_raises_the_alarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taken = _taken(monkeypatch)

    made = monitor.tiles(taken, (Panel(sensor_id="cryo-77k", warn_below=80.0),))

    assert made[0].alarm is not None


def test_a_reading_between_the_thresholds_is_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taken = _taken(monkeypatch)

    made = monitor.tiles(
        taken, (Panel(sensor_id="cryo-77k", warn_above=80.0, warn_below=70.0),)
    )

    assert made[0].alarm is None


def test_a_sensor_that_is_not_reporting_raises_no_alarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # There is no reading to be out of range, and "nothing is arriving"
    # is a different thing to say than "it is too hot".
    taken = _taken(monkeypatch)

    made = monitor.tiles(taken, (Panel(sensor_id="nowhere", warn_above=1.0),))

    assert made[0].alarm is None
    assert made[0].found is False


def _quiet(
    monkeypatch: pytest.MonkeyPatch, value: float | None = 4.2
) -> "monitor.Snapshot":
    """A snapshot whose roster remembers a sensor the query did not return."""
    from labmon.cli.roster import Known, cache_path, merge, save

    save(
        cache_path(),
        merge(
            {},
            [
                Known(
                    sensor_id="departed",
                    measurement="temperature",
                    unit="K",
                    last_seen=datetime.now(UTC) - timedelta(days=3),
                    value=value,
                )
            ],
        ),
    )
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    return monitor.take(None, None, "15m")


def test_a_quiet_tile_shows_what_the_sensor_was_last_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An empty tile answers none of the questions asked of an instrument
    # that stopped. What it was reading when it stopped answers most.
    taken = _quiet(monkeypatch)

    made = monitor.tiles(taken, (Panel(sensor_id="departed", title="Gone"),))

    assert made[0].reading == "4.2"
    assert made[0].unit == "K"
    assert "ago" in made[0].age


def test_a_quiet_tile_is_still_marked_as_not_reporting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # It carries a number, so something has to say the number is not
    # current — that is what `found` drives on screen.
    taken = _quiet(monkeypatch)

    made = monitor.tiles(taken, (Panel(sensor_id="departed"),))

    assert made[0].found is False


def test_a_remembered_reading_never_raises_the_alarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A threshold is a claim about the experiment right now, and a
    # three-day-old number cannot support one. The tile is already
    # marked as not reporting, which is the accurate alarm to raise.
    taken = _quiet(monkeypatch)

    made = monitor.tiles(taken, (Panel(sensor_id="departed", warn_above=1.0),))

    assert made[0].alarm is None
    assert made[0].reading == "4.2"


def test_a_quiet_sensor_with_nothing_remembered_shows_no_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A roster written before readings were remembered.
    taken = _quiet(monkeypatch, value=None)

    made = monitor.tiles(taken, (Panel(sensor_id="departed"),))

    assert made[0].reading == ""
    assert made[0].found is False


def test_a_tile_carries_its_age_and_freshness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taken = _taken(monkeypatch)

    made = monitor.tiles(taken, (Panel(sensor_id="abandoned"),))

    assert "ago" in made[0].age
    assert made[0].state is Freshness.STALE


# --------------------------------------------------------------------------
# Tiles on screen
# --------------------------------------------------------------------------

_LAYOUT = """
window = "15m"

[[panels]]
sensor_id = "cryo-77k"
title = "Cold finger"
precision = 3
warn_above = 70.0

[[panels]]
sensor_id = "nowhere"
title = "Missing"
"""


def _layout(tmp_path: Path) -> Path:
    path = tmp_path / "bakeout.toml"
    _ = path.write_text(_LAYOUT)
    return path


def test_a_layout_draws_tiles_instead_of_the_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel, Stat
    from labmon.config import load_monitor

    settings = load_monitor(_layout(tmp_path))

    async def scenario() -> None:
        app = Panel(
            measurements=None,
            sensor_ids=None,
            window=settings.window,
            refresh=3600.0,
            panels=settings.panels,
        )
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause()
            await pilot.pause()
            drawn = list(app.query(Stat))
            assert len(drawn) == 2
            # A table would have been mounted instead.
            assert not app.query("#table")

    _drive(scenario)


def test_every_tile_has_a_width_to_be_read_at(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A grid whose columns are `auto` collapses its children to zero
    # width, which mounts six tiles nobody can see.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel, Stat
    from labmon.config import load_monitor

    settings = load_monitor(_layout(tmp_path))

    async def scenario() -> None:
        app = Panel(
            measurements=None,
            sensor_ids=None,
            window=settings.window,
            refresh=3600.0,
            panels=settings.panels,
        )
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause()
            await pilot.pause()
            for tile in app.query(Stat):
                assert tile.region.width > 10
                assert tile.region.height > 4

    _drive(scenario)


def test_a_tile_over_its_threshold_is_marked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel, Stat
    from labmon.config import load_monitor

    settings = load_monitor(_layout(tmp_path))

    async def scenario() -> None:
        app = Panel(
            measurements=None,
            sensor_ids=None,
            window=settings.window,
            refresh=3600.0,
            panels=settings.panels,
        )
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause()
            await pilot.pause()
            classes = [tile.classes for tile in app.query(Stat)]
            assert "alarm" in classes[0]
            assert "missing" in classes[1]

    _drive(scenario)


def test_the_config_flag_supplies_the_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    built: list[dict[str, object]] = []

    class Recording:
        def __init__(self, **kwargs: object) -> None:
            built.append(kwargs)

        def run(self) -> None:
            return None

    monkeypatch.setattr("labmon.cli.tui.Panel", Recording)

    result = _run("monitor", "--config", str(_layout(tmp_path)))

    assert result.exit_code == 0, result.output
    assert built[0]["window"] == "15m"
    panels = cast(tuple[Panel, ...], built[0]["panels"])
    assert [panel.sensor_id for panel in panels] == ["cryo-77k", "nowhere"]


def test_a_layout_file_that_is_not_there_is_reported(tmp_path: Path) -> None:
    result = _run("monitor", "--config", str(tmp_path / "nope.toml"))

    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_a_tile_naming_its_measurement_takes_that_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # How a probe that writes both a temperature and a pressure says
    # which of the two this tile is for.
    taken = _taken(monkeypatch)

    made = monitor.tiles(
        taken, (Panel(sensor_id="cryo-77k", measurement="temperature"),)
    )

    assert made[0].measurement == "temperature"
    assert made[0].found is True


def test_a_tile_naming_a_measurement_that_sensor_does_not_write_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taken = _taken(monkeypatch)

    made = monitor.tiles(taken, (Panel(sensor_id="cryo-77k", measurement="pressure"),))

    assert made[0].found is False


def test_a_tile_told_nothing_shows_the_reading_as_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The same treatment the fallback table gives.
    taken = _taken(monkeypatch)

    made = monitor.tiles(taken, (Panel(sensor_id="cryo-77k"),))

    assert made[0].reading == "77.01"


def test_a_tile_shows_its_reading_without_any_statistics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A result fetched without `--stats` carries no average or deviation
    # at all, and the reading is unaffected either way.
    class NoStats(PanelClient):
        @override
        def query(self, query: str, *args: object, **kwargs: object) -> pa.Table:
            table = super().query(query, *args, **kwargs)  # pyright: ignore[reportArgumentType]
            if "sd" in table.column_names:
                return table.drop_columns(["mean", "sd", "n"])
            return table

    monkeypatch.setattr("labmon.influx.get_client", NoStats)
    taken = monitor.take(None, None, "15m")

    made = monitor.tiles(taken, (Panel(sensor_id="cryo-77k"),))

    assert made[0].reading == "77.01"


def test_a_fresh_tile_carries_no_state_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Colouring everything colours nothing; a healthy tile is plain.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel as App
    from labmon.cli.tui import Stat

    layout = tmp_path / "plain.toml"
    _ = layout.write_text('[[panels]]\nsensor_id = "cryo-77k"\n')
    from labmon.config import load_monitor

    settings = load_monitor(layout)

    async def scenario() -> None:
        app = App(
            measurements=None,
            sensor_ids=None,
            window="15m",
            refresh=3600.0,
            panels=settings.panels,
        )
        async with app.run_test(size=(110, 20)) as pilot:
            await pilot.pause()
            await pilot.pause()
            tile = next(iter(app.query(Stat)))
            assert "alarm" not in tile.classes
            assert "missing" not in tile.classes
            assert "stale" not in tile.classes

    _drive(scenario)


def test_a_stale_tile_is_marked_without_being_an_alarm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel as App
    from labmon.cli.tui import Stat

    layout = tmp_path / "old.toml"
    _ = layout.write_text('[[panels]]\nsensor_id = "abandoned"\n')
    from labmon.config import load_monitor

    settings = load_monitor(layout)

    async def scenario() -> None:
        app = App(
            measurements=None,
            sensor_ids=None,
            window="15m",
            refresh=3600.0,
            panels=settings.panels,
        )
        async with app.run_test(size=(110, 20)) as pilot:
            await pilot.pause()
            await pilot.pause()
            tile = next(iter(app.query(Stat)))
            assert "stale" in tile.classes
            assert "alarm" not in tile.classes

    _drive(scenario)


def test_a_tile_for_a_sensor_with_one_reading_still_draws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A single sample in the window gives a NULL sample deviation, which
    # renders as a blank cell. Everything downstream has to cope with
    # that rather than take the tick down.
    class OneSample(PanelClient):
        @override
        def query(self, query: str, *args: object, **kwargs: object) -> pa.Table:
            table = super().query(query, *args, **kwargs)  # pyright: ignore[reportArgumentType]
            if "sd" not in table.column_names:
                return table
            return table.drop_columns(["sd"]).append_column(
                "sd", pa.array([None, None], pa.float64())
            )

    monkeypatch.setattr("labmon.influx.get_client", OneSample)
    taken = monitor.take(None, None, "15m")

    made = monitor.tiles(taken, (Panel(sensor_id="cryo-77k"),))

    assert made[0].reading == "77.01"


# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------


def test_the_cadence_ladder_carries_the_configured_rate() -> None:
    assert 3.0 in monitor.cadences(3.0)
    assert monitor.cadences(2.0) == monitor.RATES


def test_the_menu_is_in_order_and_free_of_duplicates() -> None:
    offered = monitor.cadences(5.0)

    assert list(offered) == sorted(set(offered))


def test_the_keys_list_is_built_from_the_bindings() -> None:
    # Written out by hand it would drift from the bindings the moment
    # one of them changed, which is exactly when it is read.
    from labmon.cli.tui import Panel as App
    from labmon.cli.tui import keys_table

    rendered = _to_text(keys_table(App.BINDINGS), width=80)

    assert "?" in rendered
    assert "Menu" not in rendered  # the tooltip, not the terse footer label
    assert "command palette" in rendered


def test_a_binding_written_as_a_tuple_is_still_listed() -> None:
    from labmon.cli.tui import keys_table

    rendered = _to_text(keys_table([("z", "zap", "Zap")]), width=80)

    assert "z" in rendered
    assert "Zap" in rendered


def test_question_mark_opens_the_keys_and_escape_closes_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Textual's own help panel has no binding to close it: opened from
    # the menu it can only be dismissed from the menu.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Keys
    from labmon.cli.tui import Panel as App

    async def scenario() -> None:
        app = App(measurements=None, sensor_ids=None, window="15m", refresh=3600.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("?")
            await pilot.pause()
            assert isinstance(app.screen, Keys)

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, Keys)

    _drive(scenario)


def test_q_closes_the_keys_rather_than_the_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Quitting to get a help screen off the panel is the failure this
    # replaces, so the most obvious key has to dismiss it.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Keys
    from labmon.cli.tui import Panel as App

    async def scenario() -> None:
        app = App(measurements=None, sensor_ids=None, window="15m", refresh=3600.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("?")
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()

            assert not isinstance(app.screen, Keys)
            assert app.is_running

    _drive(scenario)


def test_the_footer_offers_the_panels_own_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel as App

    async def scenario() -> None:
        app = App(measurements=None, sensor_ids=None, window="15m", refresh=3600.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            shown = {
                active.binding.key: active.binding.description
                for active in app.screen.active_bindings.values()
                if active.binding.show
            }

            assert shown == {
                "q": "Quit",
                "r": "Change refresh rate",
                "m": "Menu",
                "question_mark": "Keys",
            }

    _drive(scenario)


# --------------------------------------------------------------------------
# Per-sensor display rules
# --------------------------------------------------------------------------


def _valued(snapshot: "monitor.Snapshot", sensor: str) -> str:
    """One sensor's value cell, as the panel writes it."""
    at = snapshot.columns.index("value")
    row = next(row for row in snapshot.rows if row.sensor_id == sensor)
    return row.cells[at]


def test_a_display_rule_fixes_the_digits_in_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `cryo-77k` reads 77.01 against a spread of 0.031, so the automatic
    # rule quotes it to three decimals. A rule saying one wins.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    taken = monitor.take(
        None, None, "15m", display=(Display(sensor_id="cryo-77k", precision=1),)
    )

    assert _valued(taken, "cryo-77k") == "77.0"


def test_a_display_rule_can_force_scientific_notation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    taken = monitor.take(
        None,
        None,
        "15m",
        display=(Display(sensor_id="cryo-77k", format="scientific"),),
    )

    assert "e+" in _valued(taken, "cryo-77k")


def test_a_display_rule_leaves_other_sensors_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    taken = monitor.take(
        None, None, "15m", display=(Display(sensor_id="cryo-77k", precision=1),)
    )

    assert _valued(taken, "abandoned") == "4.2"


def test_a_display_rule_saying_nothing_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A rule naming only a sensor is how somebody starts writing one.
    # It must not quietly become `precision = 0`.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    plain = monitor.take(None, None, "15m")
    ruled = monitor.take(None, None, "15m", display=(Display(sensor_id="cryo-77k"),))

    assert _valued(ruled, "cryo-77k") == _valued(plain, "cryo-77k")


def test_a_display_rule_reaches_a_quiet_sensor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The rule is about the instrument, not about the window, so it
    # applies to a reading the roster remembered.
    from labmon.cli.roster import Known, cache_path, merge, save

    save(
        cache_path(),
        merge(
            {},
            [
                Known(
                    sensor_id="departed",
                    measurement="temperature",
                    unit="K",
                    last_seen=datetime.now(UTC) - timedelta(days=3),
                    value=4.196,
                )
            ],
        ),
    )
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    taken = monitor.take(
        None, None, "15m", display=(Display(sensor_id="departed", precision=1),)
    )

    assert _valued(taken, "departed") == "4.2"


def test_a_tile_precision_wins_over_the_sensor_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The rule says how the instrument is worth reading everywhere; the
    # tile says how this one tile wants it.
    taken = _taken(monkeypatch)

    made = monitor.tiles(
        taken,
        (Panel(sensor_id="cryo-77k", precision=4),),
        (Display(sensor_id="cryo-77k", precision=1),),
    )

    assert made[0].reading == "77.0100"


def test_a_tile_without_a_precision_takes_the_sensor_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taken = _taken(monkeypatch)

    made = monitor.tiles(
        taken,
        (Panel(sensor_id="cryo-77k"),),
        (Display(sensor_id="cryo-77k", precision=1),),
    )

    assert made[0].reading == "77.0"


def test_the_plain_table_is_never_offered_display_rules() -> None:
    # `labmon query latest` promises the reading exactly as stored, and
    # is the escape hatch the panel's rounding points at. That promise is
    # kept by never handing rules to the renderer at all, rather than by
    # the renderer declining to apply the ones it was given — a signature
    # that cannot carry them cannot be made to close the hatch by accident.
    import inspect

    from labmon.cli.render import render_latest

    assert "display" not in inspect.signature(render_latest).parameters


def test_a_display_rule_reaches_live_rows_without_statistics() -> None:
    # The rule used to be applied from inside `_summary`, which returns
    # early on a table carrying no `mean`. A stats-less table given rules
    # therefore formatted its silent rows and not its live ones, writing
    # one sensor two ways in the same table.
    from labmon.cli.render import latest_rows

    table = pa.table(
        {
            "measurement": pa.array(["temperature"]),
            "sensor_id": pa.array(["cryo-77k"]),
            "time": pa.array([_NOW], pa.timestamp("ms", tz="UTC")),
            "value": pa.array([77.0123456]),
            "unit": pa.array(["K"]),
        }
    )

    columns, rows = latest_rows(
        table, _NOW, display=(Display(sensor_id="cryo-77k", precision=1),)
    )

    assert rows[0].cells[columns.index("value")] == "77.0"


def test_a_live_row_and_a_silent_one_are_written_the_same_way() -> None:
    # The two cells come from different code paths, and the rule is the
    # same fact about the same instrument in both.
    from labmon.cli.render import latest_rows
    from labmon.cli.roster import Known

    table = pa.table(
        {
            "measurement": pa.array(["temperature"]),
            "sensor_id": pa.array(["cryo-77k"]),
            "time": pa.array([_NOW], pa.timestamp("ms", tz="UTC")),
            "value": pa.array([77.0123456]),
            "unit": pa.array(["K"]),
        }
    )
    gone = Known(
        sensor_id="cryo-4k",
        measurement="temperature",
        unit="K",
        last_seen=_NOW - timedelta(hours=3),
        value=4.0987654,
    )

    columns, rows = latest_rows(
        table,
        _NOW,
        silent=(gone,),
        display=(
            Display(sensor_id="cryo-77k", precision=1),
            Display(sensor_id="cryo-4k", precision=1),
        ),
    )

    written = {row.sensor_id: row.cells[columns.index("value")] for row in rows}
    assert written == {"cryo-77k": "77.0", "cryo-4k": "4.1"}
