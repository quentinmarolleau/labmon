"""`labmon sensors` — what labmon remembers, and how to correct it."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pytest

from labmon.cli.main import build_app
from labmon.cli.roster import Known, load, merge, save
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
            # `table_name` is named in every row, as the server names it,
            # so the same answer serves the sweep over every table and
            # the question about one.
            columns = ["time", "value", "sensor_id", "unit"]
            return pa.table(
                {
                    "table_name": pa.array(["temperature"] * len(columns)),
                    "column_name": pa.array(columns),
                }
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
    entry = Known(
        sensor_id=sensor,
        measurement="temperature",
        unit="K",
        last_seen=datetime.now(UTC) - timedelta(days=days),
    )
    save(path, merge(load(path), [entry]))


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


def test_refresh_rebuilds_from_the_database(
    roster: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", RosterClient)

    result = _run("sensors", "--refresh")

    assert result.exit_code == 0
    assert ("cryo-77k", "temperature") in load(roster)


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

    assert ("old-probe", "temperature") in load(roster)


def test_forget_removes_a_decommissioned_sensor(roster: Path) -> None:
    _remember(roster, "old-probe")

    result = _run("sensors", "--forget", "old-probe")

    assert result.exit_code == 0
    assert ("old-probe", "temperature") not in load(roster)


def test_forgetting_an_unknown_sensor_fails_rather_than_pretending(
    roster: Path,
) -> None:
    # Succeeding silently leaves somebody believing they removed a sensor
    # that is still listed under a name they mistyped.
    _remember(roster, "old-probe")

    result = _run("sensors", "--forget", "typo")

    assert result.exit_code != 0
    assert ("old-probe", "temperature") in load(roster)


def test_forget_and_refresh_together_are_refused(roster: Path) -> None:
    # Refreshing would re-add whatever was just forgotten if it still
    # reports, so the two together have no coherent meaning.
    _remember(roster, "old-probe")

    result = _run("sensors", "--refresh", "--forget", "old-probe")

    assert result.exit_code != 0


def test_a_plain_listing_does_not_claim_to_know_what_is_live(roster: Path) -> None:
    # Without --refresh nothing is asked of the database, so a `source`
    # column could only ever say "cached" — including for sensors
    # reporting right now. Better to omit a column than to print one
    # that is misleading.
    _remember(roster, "old-probe")

    result = _run("sensors")

    assert "source" not in result.output


def test_a_refresh_does_report_what_is_live(
    roster: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _remember(roster, "old-probe")
    monkeypatch.setattr("labmon.influx.get_client", RosterClient)

    result = _run("sensors", "--refresh")

    assert "source" in result.output
    assert "live" in result.output


def test_a_listing_can_be_narrowed_to_one_sensor(roster: Path) -> None:
    _remember(roster, "old-probe")
    _remember(roster, "other-probe")

    result = _run("sensors", "--sensor-id", "old-probe")

    assert "old-probe" in result.output
    assert "other-probe" not in result.output


def test_a_listing_can_be_narrowed_to_one_measurement(roster: Path) -> None:
    known = load(roster)
    known[("gauge", "pressure")] = Known(
        sensor_id="gauge",
        measurement="pressure",
        unit="mbar",
        last_seen=datetime.now(UTC),
    )
    save(roster, known)
    _remember(roster, "thermo")

    result = _run("sensors", "--measurement", "temperature")

    assert "thermo" in result.output
    assert "gauge" not in result.output


def test_a_window_without_a_refresh_is_refused(roster: Path) -> None:
    # --since only bounds the query a refresh runs; accepting it for a
    # plain listing would let somebody believe it had narrowed something.
    _remember(roster, "old-probe")

    result = _run("sensors", "--since", "1w")

    assert result.exit_code != 0


def test_forgetting_a_sensor_that_is_still_writing_says_so(
    roster: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The cache may only ever add, so a sensor still reporting will be
    # remembered again by the next query. `forgot X` alone would read as
    # "it is gone" for a sensor that is not.
    _remember(roster, "cryo-77k")
    monkeypatch.setattr("labmon.influx.get_client", RosterClient)

    result = _run("sensors", "--forget", "cryo-77k")

    assert result.exit_code == 0
    assert ("cryo-77k", "temperature") not in load(roster)
    assert "still has readings in temperature" in result.output


def test_forgetting_a_silent_sensor_adds_nothing(
    roster: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # RosterClient only ever reports cryo-77k, so the database agrees
    # that old-probe is gone and there is nothing to warn about.
    _remember(roster, "old-probe")
    monkeypatch.setattr("labmon.influx.get_client", RosterClient)

    result = _run("sensors", "--forget", "old-probe")

    assert result.exit_code == 0
    assert "still has readings" not in result.output


def test_forgetting_works_with_the_database_unreachable(
    roster: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The roster is already written by the time the check runs, so a
    # database that is not there cannot turn a finished command into a
    # failed one.
    _remember(roster, "old-probe")

    def refuse() -> object:
        raise OSError("no route to host")

    monkeypatch.setattr("labmon.influx.get_client", refuse)

    result = _run("sensors", "--forget", "old-probe")

    assert result.exit_code == 0
    assert ("old-probe", "temperature") not in load(roster)


def test_the_roster_is_ordered_by_measurement_then_sensor(roster: Path) -> None:
    # The same order as `query latest`. Three views of one kind of table
    # sorting three different ways makes a sensor hard to find in
    # whichever one you were not looking at.
    for name, measurement, days in [
        ("vac-1", "pressure", 1),
        ("room-1", "temperature", 5),
        ("cryo-77k", "temperature", 2),
        ("chamber-1", "pressure", 9),
    ]:
        entry = Known(
            sensor_id=name,
            measurement=measurement,
            unit="x",
            last_seen=datetime.now(UTC) - timedelta(days=days),
        )
        save(roster, merge(load(roster), [entry]))

    listed = [
        line.split()[0]
        for line in _run("sensors").output.splitlines()
        if " ago" in line
    ]

    assert listed == ["chamber-1", "vac-1", "cryo-77k", "room-1"]
