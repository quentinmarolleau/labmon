"""`labmon sensors` — what labmon remembers, and how to correct it."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pytest

from labmon.cli.main import build_app
from labmon.cli.roster import Known, load, save
from tests.cli_runner import Invocation, invoke


def _run(*args: str) -> Invocation:
    return invoke(build_app(), list(args))


class RosterClient:
    """One live sensor, so a cached one has nothing to match."""

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
        return pa.table(
            {
                "measurement": pa.array(["temperature"]),
                "sensor_id": pa.array(["cryo-77k"]),
                "time": pa.array(
                    [datetime.now(UTC) - timedelta(seconds=3)],
                    pa.timestamp("ms", tz="UTC"),
                ),
                "value": pa.array([77.01]),
                "unit": pa.array(["K"]),
            }
        )

    def close(self) -> None:
        return None


@pytest.fixture
def roster(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path / "labmon" / "sensors.json"


def _remember(path: Path, sensor: str, days: int = 2) -> None:
    known = load(path)
    known[sensor] = Known(
        sensor_id=sensor,
        measurement="temperature",
        unit="K",
        last_seen=datetime.now(UTC) - timedelta(days=days),
    )
    save(path, known)


@pytest.mark.usefixtures("roster")
def test_an_empty_roster_says_so_rather_than_printing_a_bare_header() -> None:
    result = _run("sensors")

    assert result.exit_code == 0
    assert "nothing" in result.output.lower()


def test_sensors_lists_what_is_remembered(roster: Path) -> None:
    _remember(roster, "old-probe")

    result = _run("sensors")

    assert result.exit_code == 0
    assert "old-probe" in result.output


def test_a_remembered_sensor_is_marked_as_cached(roster: Path) -> None:
    # The source column is the point: it says outright whether a sensor
    # is reporting now or is only remembered.
    _remember(roster, "old-probe")

    result = _run("sensors")

    assert "cached" in result.output


def test_refresh_rebuilds_from_the_database(
    roster: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", RosterClient)

    result = _run("sensors", "--refresh")

    assert result.exit_code == 0
    assert "cryo-77k" in load(roster)


@pytest.mark.usefixtures("roster")
def test_a_refreshed_sensor_is_marked_as_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", RosterClient)

    result = _run("sensors", "--refresh")

    assert "live" in result.output


def test_refresh_keeps_a_sensor_the_database_no_longer_has(
    roster: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A refresh is a union too. Dropping what the window did not cover
    # would delete exactly the silence worth keeping.
    _remember(roster, "old-probe")
    monkeypatch.setattr("labmon.influx.get_client", RosterClient)

    _ = _run("sensors", "--refresh")

    assert "old-probe" in load(roster)


def test_forget_removes_a_decommissioned_sensor(roster: Path) -> None:
    _remember(roster, "old-probe")

    result = _run("sensors", "--forget", "old-probe")

    assert result.exit_code == 0
    assert "old-probe" not in load(roster)


def test_forgetting_an_unknown_sensor_fails_rather_than_pretending(
    roster: Path,
) -> None:
    # Succeeding silently leaves somebody believing they removed a sensor
    # that is still listed under a name they mistyped.
    _remember(roster, "old-probe")

    result = _run("sensors", "--forget", "typo")

    assert result.exit_code != 0
    assert "old-probe" in load(roster)


def test_forget_and_refresh_together_are_refused(roster: Path) -> None:
    # Refreshing would re-add whatever was just forgotten if it still
    # reports, so the two together have no coherent meaning.
    _remember(roster, "old-probe")

    result = _run("sensors", "--refresh", "--forget", "old-probe")

    assert result.exit_code != 0
