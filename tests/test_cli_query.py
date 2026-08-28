"""`labmon query` and the terminal table it prints."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import override
from zoneinfo import ZoneInfo

import pyarrow as pa
import pytest

from labmon.cli import selection
from labmon.cli.main import build_app
from labmon.cli.render import (
    DEFAULT_LIMIT,
    latest_rows,
    render,
    render_latest,
    visible_columns,
)
from labmon.cli.runtime import REFUSED
from labmon.export.table import combine, normalise
from tests.cli_runner import Invocation, invoke


def _run(*args: str) -> Invocation:
    """Invoke a labmon command, typed so the result's fields resolve."""
    return invoke(build_app(), list(args))


def _readings(count: int = 3, unit: str | None = "K") -> pa.Table:
    columns = {
        "time": pa.array([i * 1_000_000_000 for i in range(count)], pa.timestamp("ns")),
        "sensor_id": pa.array([f"sensor-{i}" for i in range(count)]),
        "value": pa.array([float(i) for i in range(count)]),
    }
    if unit is not None:
        columns["unit"] = pa.array([unit] * count)
    return combine([normalise(pa.table(columns), "temperature")])


def test_an_empty_result_says_so_rather_than_printing_a_bare_header() -> None:
    assert render(combine([])) == "no readings matched"


def test_the_header_names_every_visible_column() -> None:
    header = render(_readings()).splitlines()[0]

    assert header.split() == ["time", "sensor_id", "measurement", "value", "unit"]


def test_a_column_that_is_entirely_empty_is_left_out() -> None:
    # A measurement written by something else may carry no unit at all,
    # and a column of blanks takes width while saying nothing.
    assert "unit" not in visible_columns(_readings(unit=None))


def test_provenance_columns_stay_out_of_the_terminal() -> None:
    # They are in every exported file; a hash column here would push the
    # value off the screen.
    columns = visible_columns(_readings())

    assert "calibration_id" not in columns
    assert "input_volts" not in columns


def test_the_most_recent_readings_are_the_ones_kept() -> None:
    rendered = render(_readings(count=5), limit=2)

    assert "sensor-3" in rendered
    assert "sensor-4" in rendered
    assert "sensor-0" not in rendered


def test_truncation_is_stated_rather_than_silent() -> None:
    # A quietly shortened table is one somebody draws a conclusion from.
    rendered = render(_readings(count=5), limit=2)

    assert "showing the last 2 of 5 readings" in rendered


def test_an_untruncated_table_reports_its_size() -> None:
    assert "3 readings" in render(_readings(count=3), limit=DEFAULT_LIMIT)


def test_one_reading_is_not_pluralised() -> None:
    assert "1 reading\n" in render(_readings(count=1)) + "\n"


def test_a_limit_of_zero_shows_everything() -> None:
    rendered = render(_readings(count=5), limit=0)

    assert "sensor-0" in rendered
    assert "5 readings" in rendered


def test_columns_line_up() -> None:
    lines = render(_readings(count=3)).splitlines()
    starts = [line.index("temperature") for line in lines[2:5]]

    assert len(set(starts)) == 1


def test_the_value_is_shown_exactly_as_stored() -> None:
    # Sensors already round to the resolution they claim, so the terminal
    # should neither invent digits nor hide them.
    table = combine(
        [
            normalise(
                pa.table(
                    {
                        "time": pa.array([0], pa.timestamp("ns")),
                        "sensor_id": pa.array(["a"]),
                        "value": pa.array([76.85]),
                    }
                ),
                "temperature",
            )
        ]
    )

    assert "76.85" in render(table)


class FakeClient:
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
                "time": pa.array([0], pa.timestamp("ns")),
                "value": pa.array([77.01]),
                "sensor_id": pa.array(["cryo-77k"]),
                "unit": pa.array(["K"]),
            }
        )

    def close(self) -> None:
        return None


def test_query_prints_to_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("labmon.influx.get_client", FakeClient)

    result = _run("query", "--since", "1h")

    assert result.exit_code == 0, result.output
    assert "cryo-77k" in result.output
    assert "77.01" in result.output


def test_query_and_export_accept_the_same_selection_flags() -> None:
    # The two commands ask the database the same question; if their flags
    # drift, one of the two documented invocations stops working. The
    # options are declared once in labmon.cli.options and named by both.
    shared = {"--measurement", "--sensor-id", "--since", "--until"}

    for command in ("query", "export"):
        helped = _run(command, "--help")
        assert helped.exit_code == 0
        for flag in shared:
            assert flag in helped.output, f"{command} is missing {flag}"


def test_completion_is_offered_by_typer() -> None:
    # Typer builds --install-completion from the app itself, so there is
    # nothing to keep in step with the commands.
    result = _run("--help")

    assert "--install-completion" in result.output


def test_selection_closes_the_client_when_the_query_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []

    class Failing(FakeClient):
        @override
        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr("labmon.influx.get_client", Failing)
    with pytest.raises(Exception):  # noqa: B017
        _ = selection.read(["nope"], [], "1h", None)

    assert closed == [True]


def test_a_gap_in_a_column_renders_as_blank_rather_than_none() -> None:
    # A column that is null in *some* rows survives `visible_columns`, so
    # the blank has to be rendered rather than printed as "None". Two
    # measurements in one result do this: one records a unit, one does not.
    mixed = combine(
        [
            normalise(
                pa.table(
                    {
                        "time": pa.array([0], pa.timestamp("ns")),
                        "sensor_id": pa.array(["a"]),
                        "value": pa.array([1.0]),
                        "unit": pa.array(["K"]),
                    }
                ),
                "temperature",
            ),
            normalise(
                pa.table(
                    {
                        "time": pa.array([1_000_000_000], pa.timestamp("ns")),
                        "sensor_id": pa.array(["b"]),
                        "value": pa.array([2.0]),
                    }
                ),
                "probe",
            ),
        ]
    )

    rendered = render(mixed)

    assert "unit" in rendered
    assert "None" not in rendered


def test_a_measurement_without_a_sensor_still_renders() -> None:
    table = combine(
        [
            normalise(
                pa.table(
                    {
                        "time": pa.array([0], pa.timestamp("ns")),
                        "sensor_id": pa.array([None], pa.string()),
                        "value": pa.array([1.0]),
                    }
                ),
                "probe",
            )
        ]
    )

    assert "probe" in render(table)


def test_an_unreachable_server_is_reported_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The client raises with a multi-line gRPC dump attached. As an
    # uncaught exception that fills the terminal and buries the one fact
    # that matters: which host was tried.
    from influxdb_client_3.exceptions.exceptions import InfluxDB3ClientQueryError

    from labmon.cli import main as cli_main

    def unreachable() -> object:
        raise InfluxDB3ClientQueryError(
            "Error while executing query: Flight returned unavailable error,"
            + " with message: failed to connect. gRPC client debug context:"
            + " UNKNOWN:Error received from peer {grpc_status:14}"
        )

    monkeypatch.setattr("labmon.influx.get_client", unreachable)
    monkeypatch.setattr("sys.argv", ["labmon", "query", "--since", "1h"])

    with pytest.raises(SystemExit) as exit_info:
        cli_main.main()

    assert exit_info.value.code == 3
    written = capsys.readouterr().err
    assert "cannot reach the database" in written
    assert "grpc_status" not in written


def test_an_unreachable_server_names_the_host_that_was_tried(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The usual cause is a command run against the wrong host, so the
    # host is the useful half of the message.
    from influxdb_client_3.exceptions.exceptions import InfluxDB3ClientQueryError

    from labmon.cli import main as cli_main

    def unreachable() -> object:
        raise InfluxDB3ClientQueryError("nope")

    monkeypatch.setenv("INFLUXDB_HOST", "http://elsewhere:8181")
    monkeypatch.setattr("labmon.influx.get_client", unreachable)
    monkeypatch.setattr("sys.argv", ["labmon", "query"])

    with pytest.raises(SystemExit):
        cli_main.main()

    assert "http://elsewhere:8181" in capsys.readouterr().err


def test_a_missing_token_says_which_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from labmon.cli import main as cli_main

    monkeypatch.delenv("INFLUXDB3_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("sys.argv", ["labmon", "query"])

    with pytest.raises(SystemExit) as exit_info:
        cli_main.main()

    assert exit_info.value.code == 3
    written = capsys.readouterr().err
    assert "INFLUXDB3_AUTH_TOKEN" in written
    assert "Traceback" not in written


def test_an_unrelated_key_error_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only the missing-token KeyError is translated; anything else is a
    # bug, and hiding it behind a tidy message would lose the traceback.
    from labmon.cli import main as cli_main

    def explode() -> object:
        raise KeyError("something else entirely")

    monkeypatch.setattr("labmon.influx.get_client", explode)
    monkeypatch.setattr("sys.argv", ["labmon", "query"])

    with pytest.raises(KeyError, match="something else entirely"):
        cli_main.main()


def test_the_command_line_loads_without_the_heavy_libraries() -> None:
    """Importing the CLI must not drag in pyarrow, pint or the client.

    Every `labmon --help` and every tab completion pays for whatever this
    import costs, twice per Tab press, and none of those three is needed
    to print help or list flags. Deferring them took startup from 979ms
    to about 110ms; without a test the next module-level import puts it
    back and nobody notices until the CLI feels slow again.
    """
    import subprocess
    import sys

    probe = (
        "import sys, labmon.cli.main;"
        "heavy = {'pyarrow', 'pint', 'influxdb_client_3', 'numpy', 'serial'};"
        "print(','.join(sorted(heavy & {m.split('.')[0] for m in sys.modules})))"
    )
    # This interpreter, running a literal probe.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "", (
        f"labmon.cli.main now imports {result.stdout.strip()} at module scope."
        + " Move it inside the function that needs it."
    )


def _at(millis: int) -> "pa.Table":
    """One reading, timed the way `normalise` leaves it: UTC, in ms."""
    return combine(
        [
            pa.table(
                {
                    "time": pa.array([millis], pa.timestamp("ms", tz="UTC")),
                    "sensor_id": pa.array(["a"]),
                    "value": pa.array([1.0]),
                    "unit": pa.array(["K"]),
                }
            )
        ]
    )


def test_a_whole_second_timestamp_keeps_its_milliseconds() -> None:
    # `str(datetime)` drops the fractional part when microseconds are
    # zero, so a fixed-width slice of it cut into the timezone suffix
    # and printed "00:00:01+00:". Sensors sample on whole-second grids,
    # which makes that the ordinary row rather than an edge case.
    cell = render(_at(1_000)).splitlines()[2].split()

    assert cell[0:2] == ["1970-01-01", "00:00:01.000"]


def test_a_sub_second_timestamp_shows_the_milliseconds_it_has() -> None:
    assert "00:00:01.250" in render(_at(1_250))


def test_the_timezone_suffix_is_never_half_printed() -> None:
    # Every row in a result carries the same offset, so it is dropped
    # rather than repeated — but dropped whole, not sliced through.
    assert "+00:" not in render(_at(1_000))


def test_a_configured_timezone_moves_the_printed_time() -> None:
    # Paris was CET, UTC+1, on this date. The stored instant does not
    # move; where it is shown does.
    assert "1970-01-01 01:00:01.000" in render(_at(1_000), tz=ZoneInfo("Europe/Paris"))


def test_a_shifted_time_still_does_not_print_its_offset() -> None:
    # Dropping the suffix is what makes the column narrow enough to
    # read, and it stays dropped once the zone is no longer UTC.
    assert "+01:" not in render(_at(1_000), tz=ZoneInfo("Europe/Paris"))


def test_the_timezone_reaches_the_query_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "config" / "labmon" / "labmon.toml"
    config.parent.mkdir(parents=True)
    _ = config.write_text('timezone = "Europe/Paris"\n')
    monkeypatch.setattr("labmon.influx.get_client", FakeClient)

    result = _run("query", "--since", "1h")

    assert result.exit_code == 0
    # FakeClient stamps its one reading at the epoch, which in Paris is
    # an hour later on the same day.
    assert "1970-01-01 01:00:00.000" in result.output


def test_a_broken_configuration_is_reported_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "config" / "labmon" / "labmon.toml"
    config.parent.mkdir(parents=True)
    _ = config.write_text('timezone = "Mars/Olympus"\n')
    monkeypatch.setattr("labmon.influx.get_client", FakeClient)

    result = _run("query", "--since", "1h")

    assert result.exit_code == REFUSED
    assert "Traceback" not in result.output
    assert "Mars/Olympus" in result.output


# --------------------------------------------------------------------------
# The latest reading from each sensor
# --------------------------------------------------------------------------


def _latest(rows: list[tuple[str, str, float, str, int]]) -> "pa.Table":
    """One row per sensor, `seconds` old, in the shape fetch_latest returns."""
    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    return pa.table(
        {
            "measurement": pa.array([r[1] for r in rows]),
            "sensor_id": pa.array([r[0] for r in rows]),
            "time": pa.array(
                [now - timedelta(seconds=r[4]) for r in rows],
                pa.timestamp("ms", tz="UTC"),
            ),
            "value": pa.array([r[2] for r in rows]),
            "unit": pa.array([r[3] for r in rows]),
        }
    )


_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def test_the_latest_view_has_an_age_column() -> None:
    rendered = render_latest(_latest([("a", "temperature", 1.0, "K", 3)]), now=_NOW)

    assert "age" in rendered.splitlines()[0]
    assert "3s ago" in rendered


def test_a_sensor_is_listed_once() -> None:
    table = _latest(
        [("a", "temperature", 1.0, "K", 3), ("b", "temperature", 2.0, "K", 9)]
    )

    body = [
        line for line in render_latest(table, now=_NOW).splitlines() if " ago" in line
    ]

    assert len(body) == 2


def test_rows_are_ordered_by_measurement_then_sensor() -> None:
    # Stable ordering is the whole requirement. Sorting by age looks
    # right on a one-shot listing and is unusable on a panel that
    # redraws every two seconds: every row moves, so nothing can be
    # followed. Staleness is carried by colour, which does not depend on
    # position.
    table = _latest(
        [
            ("room-1", "temperature", 1.0, "°C", 2),
            ("vac-1", "pressure", 2.0, "mbar", 9000),
            ("cryo-77k", "temperature", 3.0, "K", 40),
            ("chamber-1", "pressure", 4.0, "mbar", 5),
        ]
    )

    listed = [
        tuple(line.split()[:2])
        for line in render_latest(table, now=_NOW).splitlines()
        if " ago" in line
    ]

    assert listed == [
        ("pressure", "chamber-1"),
        ("pressure", "vac-1"),
        ("temperature", "cryo-77k"),
        ("temperature", "room-1"),
    ]


def test_a_silent_sensor_sorts_among_the_others_rather_than_at_the_end() -> None:
    # It is remembered, not exiled. Age no longer decides position, so a
    # roster entry belongs wherever its name puts it.
    from labmon.cli.roster import Known

    table = _latest([("room-1", "temperature", 1.0, "°C", 2)])
    quiet = Known(
        sensor_id="cryo-77k",
        measurement="temperature",
        unit="K",
        last_seen=_NOW - timedelta(hours=4),
    )

    listed = [
        line.split()[1]
        for line in render_latest(table, now=_NOW, silent=[quiet]).splitlines()
        if " ago" in line
    ]

    assert listed == ["cryo-77k", "room-1"]


def test_columns_stay_aligned_when_an_age_is_coloured() -> None:
    # Padding a string that carries escape codes counts them toward its
    # width, so a coloured cell silently shortens its own column.
    table = _latest(
        [
            ("a", "temperature", 1.0, "K", 2),
            ("bbbbbbbbbb", "temperature", 2.0, "K", 9000),
        ]
    )

    rendered = render_latest(table, now=_NOW, colour=True)
    plain = [
        re.sub(r"\x1b\[[0-9;]*m", "", line)
        for line in rendered.splitlines()
        if " ago" in line
    ]

    starts = {line.index("ago") for line in plain}
    assert len(starts) == 1, f"age column is ragged: {starts}"


def test_no_colour_means_no_escape_codes() -> None:
    rendered = render_latest(_latest([("a", "temperature", 1.0, "K", 2)]), now=_NOW)

    assert "\x1b[" not in rendered


def test_an_empty_latest_result_says_so() -> None:
    assert render_latest(_latest([]), now=_NOW) == "no readings matched"


class LatestClient(FakeClient):
    """Answers the latest query with one row per sensor, of differing ages."""

    @override
    def query(self, query: str, *args: object, **kwargs: object) -> pa.Table:
        if query.startswith("SELECT '"):
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
                }
            )
        return super().query(query, *args, **kwargs)  # pyright: ignore[reportArgumentType]


def test_latest_prints_one_row_per_sensor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("labmon.influx.get_client", LatestClient)

    result = _run("query", "latest")

    assert result.exit_code == 0, result.output
    assert "cryo-77k" in result.output
    assert "2 sensors" in result.output


def test_latest_reports_how_long_ago_each_sensor_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", LatestClient)

    result = _run("query", "latest")

    assert "2s ago" in result.output
    assert "3h ago" in result.output


def test_latest_orders_by_measurement_then_sensor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", LatestClient)

    body = [
        line.split()[1]
        for line in _run("query", "latest").output.splitlines()
        if " ago" in line
    ]

    assert body == ["abandoned", "cryo-77k"]


def test_latest_closes_the_client_when_the_query_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []

    class Failing(FakeClient):
        @override
        def query(self, query: str, *args: object, **kwargs: object) -> pa.Table:
            if query.startswith("SELECT '"):
                raise RuntimeError("server said no")
            return super().query(query, *args, **kwargs)  # pyright: ignore[reportArgumentType]

        @override
        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr("labmon.influx.get_client", Failing)

    result = _run("query", "latest")

    assert result.exit_code != 0
    assert closed == [True]


class QuietClient(FakeClient):
    """Returns one live sensor, so a cached one has nothing to match."""

    @override
    def query(self, query: str, *args: object, **kwargs: object) -> pa.Table:
        if query.startswith("SELECT '"):
            return pa.table(
                {
                    "measurement": pa.array(["temperature"]),
                    "sensor_id": pa.array(["cryo-77k"]),
                    "time": pa.array(
                        [datetime.now(UTC) - timedelta(seconds=2)],
                        pa.timestamp("ms", tz="UTC"),
                    ),
                    "value": pa.array([77.01]),
                    "unit": pa.array(["K"]),
                }
            )
        return super().query(query, *args, **kwargs)  # pyright: ignore[reportArgumentType]


@pytest.fixture
def roster(tmp_path: "Path", monkeypatch: pytest.MonkeyPatch) -> "Path":
    """Point the roster cache somewhere disposable."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path / "labmon" / "sensors.json"


def test_a_sensor_silent_beyond_the_window_is_still_listed(
    monkeypatch: pytest.MonkeyPatch, roster: "Path"
) -> None:
    # The whole point of the cache: without it this sensor has no row to
    # be stale, so it vanishes instead of turning red.
    from labmon.cli.roster import Known, merge, save

    save(
        roster,
        merge(
            {},
            [
                Known(
                    sensor_id="abandoned",
                    measurement="temperature",
                    unit="K",
                    last_seen=datetime.now(UTC) - timedelta(days=2),
                )
            ],
        ),
    )
    monkeypatch.setattr("labmon.influx.get_client", QuietClient)

    result = _run("query", "latest")

    assert "abandoned" in result.output
    assert "2d ago" in result.output


def test_a_silent_sensor_shows_what_it_was_last_reading(
    monkeypatch: pytest.MonkeyPatch, roster: "Path"
) -> None:
    # What an instrument was reading when it went quiet is usually the
    # question being asked of it. The age sits in the same row and says
    # the number is not current.
    from labmon.cli.roster import Known, merge, save

    save(
        roster,
        merge(
            {},
            [
                Known(
                    sensor_id="abandoned",
                    measurement="temperature",
                    unit="K",
                    last_seen=datetime.now(UTC) - timedelta(days=2),
                    value=4.2,
                )
            ],
        ),
    )
    monkeypatch.setattr("labmon.influx.get_client", QuietClient)

    line = next(
        line
        for line in _run("query", "latest").output.splitlines()
        if " abandoned " in line
    )

    assert "4.2" in line
    assert "2d ago" in line


def test_a_roster_entry_with_no_remembered_value_shows_none(
    monkeypatch: pytest.MonkeyPatch, roster: "Path"
) -> None:
    # A roster written before readings were remembered still lists its
    # sensors, which is the whole point of the cache — it just has
    # nothing to put in the value column for them.
    from labmon.cli.roster import Known, merge, save

    save(
        roster,
        merge(
            {},
            [
                Known(
                    sensor_id="abandoned",
                    measurement="temperature",
                    unit="K",
                    last_seen=datetime.now(UTC) - timedelta(days=2),
                )
            ],
        ),
    )
    monkeypatch.setattr("labmon.influx.get_client", QuietClient)

    line = next(
        line
        for line in _run("query", "latest").output.splitlines()
        if " abandoned " in line
    )

    assert line.split() == ["temperature", "abandoned", "K", "2d", "ago"]


def test_a_live_sensor_is_remembered_for_next_time(
    monkeypatch: pytest.MonkeyPatch, roster: "Path"
) -> None:
    from labmon.cli.roster import load

    monkeypatch.setattr("labmon.influx.get_client", QuietClient)

    _ = _run("query", "latest")

    assert ("cryo-77k", "temperature") in load(roster)


def test_a_row_without_a_sensor_is_left_out_of_the_roster() -> None:
    # A measurement written by something else may carry no sensor id, and
    # remembering an entry with no name would be remembering nothing.
    table = pa.table(
        {
            "measurement": pa.array(["temperature", "temperature"]),
            "sensor_id": pa.array(["a", None]),
            "time": pa.array([datetime.now(UTC), None], pa.timestamp("ms", tz="UTC")),
            "value": pa.array([1.0, 2.0]),
            "unit": pa.array(["K", "K"]),
        }
    )

    assert [entry.sensor_id for entry in selection.known_from(table)] == ["a"]


def test_a_missing_unit_column_is_remembered_as_blank() -> None:
    table = pa.table(
        {
            "sensor_id": pa.array(["a"]),
            "time": pa.array([datetime.now(UTC)], pa.timestamp("ms", tz="UTC")),
            "value": pa.array([1.0]),
        }
    )

    assert selection.known_from(table)[0].unit == ""


def test_the_roster_remembers_the_reading_that_was_current() -> None:
    table = pa.table(
        {
            "sensor_id": pa.array(["a"]),
            "measurement": pa.array(["temperature"]),
            "time": pa.array([datetime.now(UTC)], pa.timestamp("ms", tz="UTC")),
            "value": pa.array([4.2]),
            "unit": pa.array(["K"]),
        }
    )

    assert selection.known_from(table)[0].value == 4.2


def test_a_row_with_no_reading_is_remembered_without_one() -> None:
    # The sensor is still worth remembering: the roster's job is knowing
    # that it exists and has gone quiet, not what it last said.
    table = pa.table(
        {
            "sensor_id": pa.array(["a"]),
            "measurement": pa.array(["temperature"]),
            "time": pa.array([datetime.now(UTC)], pa.timestamp("ms", tz="UTC")),
            "value": pa.array([None], pa.float64()),
            "unit": pa.array(["K"]),
        }
    )

    entry = selection.known_from(table)[0]

    assert entry.value is None
    assert entry.sensor_id == "a"


def test_an_unwritable_cache_does_not_fail_the_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Everything the query returned is still correct; refusing to print
    # it because a derived file could not be written would be the wrong
    # trade.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr("labmon.influx.get_client", QuietClient)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr("labmon.cli.roster.save", refuse)

    result = _run("query", "latest")

    assert result.exit_code == 0
    assert "cryo-77k" in result.output


def test_narrowing_by_measurement_narrows_the_roster_too(
    monkeypatch: pytest.MonkeyPatch, roster: Path
) -> None:
    # Asking for temperatures and being shown a silent pressure gauge is
    # answering a question that was not asked.
    from labmon.cli.roster import Known, merge, save

    stale = datetime.now(UTC) - timedelta(days=1)
    save(
        roster,
        merge(
            {},
            [
                Known(
                    sensor_id="old-thermo",
                    measurement="temperature",
                    unit="K",
                    last_seen=stale,
                ),
                Known(
                    sensor_id="old-gauge",
                    measurement="pressure",
                    unit="mbar",
                    last_seen=stale,
                ),
            ],
        ),
    )
    monkeypatch.setattr("labmon.influx.get_client", QuietClient)

    output = _run("query", "latest", "--measurement", "temperature").output

    assert "old-thermo" in output
    assert "old-gauge" not in output


def test_query_still_prints_readings_with_no_subcommand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `latest` is an addition, not a replacement: the log-style view is
    # what `labmon query` has always meant and is documented as.
    monkeypatch.setattr("labmon.influx.get_client", FakeClient)

    result = _run("query", "--since", "1h")

    assert result.exit_code == 0, result.output
    assert "cryo-77k" in result.output


def test_latest_is_a_subcommand_rather_than_a_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A subcommand gets its own help and its own completions, and leaves
    # room for the other questions worth asking of recorded readings.
    monkeypatch.setattr("labmon.influx.get_client", LatestClient)

    result = _run("query", "--latest")

    assert result.exit_code != 0


def test_query_help_names_its_subcommand() -> None:
    result = _run("query", "--help")

    assert result.exit_code == 0
    assert "latest" in result.output


# --------------------------------------------------------------------------
# Window statistics beside the latest reading
# --------------------------------------------------------------------------


def _with_stats(
    rows: list[tuple[str, str, float, str, int]],
    stats: list[tuple[float | None, float | None, int]],
) -> "pa.Table":
    """`_latest`, plus the columns `fetch_latest(stats=True)` adds."""
    table = _latest(rows)
    return (
        table.append_column("mean", pa.array([s[0] for s in stats], pa.float64()))
        .append_column("sd", pa.array([s[1] for s in stats], pa.float64()))
        .append_column("n", pa.array([s[2] for s in stats], pa.int64()))
    )


def test_statistics_are_shown_when_the_table_carries_them() -> None:
    # Presence of the column is the request: the renderer takes no flag,
    # so there is no way for it to disagree with the query.
    table = _with_stats([("a", "temperature", 76.9, "K", 3)], [(76.85, 0.021, 900)])

    rendered = render_latest(table, now=_NOW)

    header = rendered.splitlines()[0]

    # Named as a physicist writes them, and named identically in the
    # panel — the two share one table of headings.
    assert "average" in header
    assert "σ" in header
    assert "N" in header
    assert "76.85" in rendered
    assert "900" in rendered


def test_no_statistics_columns_means_no_statistics_headers() -> None:
    rendered = render_latest(_latest([("a", "temperature", 1.0, "K", 3)]), now=_NOW)

    assert "average" not in rendered
    assert "σ" not in rendered
    assert "N" not in rendered


def test_a_statistic_is_shown_at_a_readable_magnitude() -> None:
    # A vacuum gauge's mean is no more readable in full decimal than its
    # reading. The trailing zero on the mean is significant: the spread
    # reaches a decimal place further than the mean's own digits do.
    table = _with_stats(
        [("vac", "pressure", 1.26e-8, "mbar", 1)], [(1.31e-8, 4.2e-10, 450)]
    )

    rendered = render_latest(table, now=_NOW)

    assert "1.310e-08" in rendered
    assert "4.2e-10" in rendered


def test_a_single_reading_leaves_the_deviation_blank() -> None:
    # Sample standard deviation is undefined for one reading, and the
    # server returns NULL. "0" would claim a spread that was measured.
    table = _with_stats([("a", "temperature", 1.0, "K", 3)], [(1.0, None, 1)])

    rendered = render_latest(table, now=_NOW)

    assert "1 sensor" in rendered
    assert "nan" not in rendered.lower()


def test_a_group_with_nothing_to_average_shows_blanks() -> None:
    # `avg` over a window whose values are all NULL returns NULL, and
    # the group still exists because rows are there to be grouped.
    table = _with_stats([("a", "temperature", 1.0, "K", 3)], [(None, None, 0)])

    lines = render_latest(table, now=_NOW).splitlines()
    row = next(line for line in lines if " ago" in line)

    assert row.split() == ["temperature", "a", "1.0", "K", "3s", "ago", "0"]


def test_a_silent_sensor_has_no_statistics_to_show() -> None:
    from labmon.cli.roster import Known

    table = _with_stats([("a", "temperature", 1.0, "K", 3)], [(1.0, 0.5, 90)])
    quiet = Known(
        sensor_id="gone",
        measurement="temperature",
        unit="K",
        last_seen=_NOW - timedelta(hours=4),
        value=9.5,
    )

    lines = render_latest(table, now=_NOW, silent=[quiet]).splitlines()
    row = next(line for line in lines if " gone " in line)

    # The reading it left behind, and no mean, sd or n: those describe a
    # window this sensor contributed nothing to.
    assert row.split() == ["temperature", "gone", "9.5", "K", "4h", "ago"]


class StatsClient(FakeClient):
    """Answers the latest query with statistics attached."""

    @override
    def query(self, query: str, *args: object, **kwargs: object) -> pa.Table:
        if query.startswith("SELECT '"):
            now = datetime.now(UTC)
            return pa.table(
                {
                    "measurement": pa.array(["temperature"]),
                    "sensor_id": pa.array(["cryo-77k"]),
                    "time": pa.array(
                        [now - timedelta(seconds=2)], pa.timestamp("ms", tz="UTC")
                    ),
                    "value": pa.array([77.01]),
                    "unit": pa.array(["K"]),
                    "mean": pa.array([76.98]),
                    "sd": pa.array([0.031]),
                    "n": pa.array([1800], pa.int64()),
                }
            )
        return super().query(query, *args, **kwargs)  # pyright: ignore[reportArgumentType]


def test_the_stats_flag_asks_the_database_for_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labmon.influx.get_client", StatsClient)

    result = _run("query", "latest", "--stats")

    assert result.exit_code == 0, result.output
    assert "76.98" in result.output
    assert "1800" in result.output


def test_latest_without_the_flag_asks_for_no_statistics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    class Watching(FakeClient):
        @override
        def query(self, query: str, *args: object, **kwargs: object) -> pa.Table:
            seen.append(query)
            return super().query(query, *args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr("labmon.influx.get_client", Watching)

    _ = _run("query", "latest")

    assert not any("stddev(" in query for query in seen)


def test_a_reading_is_shown_exactly_as_stored() -> None:
    # A reading is recorded, not computed: the sensor already rounded it
    # to the resolution it claims. Only the average and the deviation
    # are rounded, and only against each other.
    table = _with_stats(
        [("beam-x", "position", -7.441802197802218, "µm", 2)], [(0.0196, 16.136, 900)]
    )

    _present, rows = latest_rows(table, _NOW)

    assert "-7.441802197802218" in rows[0].cells


def test_a_wide_spread_does_not_shorten_the_reading_beside_it() -> None:
    # The beam wandered 16 µm across the window, which says nothing
    # about how well the detector reads its position. Rounding the
    # reading against that spread quoted it to whole µm, which was the
    # wrong statistic applied to the wrong number.
    table = _with_stats(
        [("beam-x", "position", -7.441802197802218, "µm", 2)], [(0.0196, 16.136, 900)]
    )

    present, rows = latest_rows(table, _NOW)

    assert rows[0].cells[present.index("value")] == "-7.441802197802218"
    # The average, computed from that same window, is rounded against it,
    # down to the single figure the floor keeps.
    assert rows[0].cells[present.index("mean")] == "0.02"


def test_the_measurement_leads_so_the_ordering_is_visible() -> None:
    # A table sorted by a column it does not show first looks arbitrary,
    # which is the readability problem the ordering was meant to fix.
    table = _latest([("room-1", "temperature", 1.0, "°C", 2)])

    present, _rows = latest_rows(table, _NOW)

    assert present[:2] == ("measurement", "sensor_id")
