"""`labmon monitor` — the panel, and the snapshot behind each tick."""

import asyncio
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

    assert "cryo-77k" in taken.body
    assert taken.error is None


def test_a_snapshot_asks_for_statistics(monkeypatch: pytest.MonkeyPatch) -> None:
    # The panel's whole point over `query latest` is showing how the
    # window behaved, not only where it ended.
    monkeypatch.setattr("labmon.influx.get_client", PanelClient)

    taken = monitor.take(None, None, "15m", now=_NOW)

    assert "mean" in taken.body
    assert "sd" in taken.body


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


def test_the_status_line_names_the_window_and_the_cadence() -> None:
    line = monitor.status(
        monitor.Snapshot(body="", taken=_NOW), window="15m", refresh=2.0
    )

    assert "15m" in line
    assert "2" in line


def test_the_status_line_leads_with_the_failure_when_there_is_one() -> None:
    line = monitor.status(
        monitor.Snapshot(body="", taken=_NOW, error="database unreachable"),
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
            assert "cryo-77k" in app.latest.body

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
            assert "cryo-77k" in app.latest.body

            def fail(*args: object, **kwargs: object) -> monitor.Snapshot:
                _ = (args, kwargs)
                return monitor.Snapshot(
                    body="", taken=datetime.now(UTC), error="unreachable"
                )

            monkeypatch.setattr("labmon.cli.tui.take", fail)
            await pilot.press("r")
            await pilot.pause()
            await pilot.pause()

            assert app.latest.error == "unreachable"
            assert "cryo-77k" in app.latest.body

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
            assert app.latest.body == ""

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

    assert sys.executable in result.output
