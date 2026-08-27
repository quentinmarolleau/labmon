"""`labmon monitor` — the panel, and the snapshot behind each tick."""

import asyncio
import io
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pyarrow as pa
import pytest

from labmon.cli import monitor
from labmon.cli.main import build_app
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


def test_r_refreshes_without_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)
    from labmon.cli.tui import Panel

    async def scenario() -> None:
        app = Panel(measurements=None, sensor_ids=None, window="15m", refresh=3600.0)
        async with app.run_test() as pilot:
            await pilot.pause()
            first = app.latest.taken
            await pilot.press("r")
            await pilot.pause()
            await pilot.pause()
            assert app.latest.taken >= first

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
            await pilot.press("r")
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


def _drawn(snapshot: "monitor.Snapshot", width: int = 100) -> str:
    """The panel rendered to plain text, without starting an app."""
    from rich.console import Console

    from labmon.cli.tui import panel

    console = Console(width=width, record=True, file=io.StringIO())
    console.print(panel(snapshot, window="15m", refresh=2.0))
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


def test_readings_are_rounded_for_a_glance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The panel is read from across a room. `labmon query latest` is the
    # one that promises the value exactly as stored.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    taken = monitor.take(None, None, "15m", now=_NOW)
    values = [row.cells[taken.columns.index("value")] for row in taken.rows]

    assert "77.01" not in values
    assert "77.010" in values


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
