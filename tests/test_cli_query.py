"""`labmon query` and the terminal table it prints."""

from typing import override

import pyarrow as pa
import pytest
from typer.testing import CliRunner, Result

from labmon.cli import selection
from labmon.cli.main import build_app
from labmon.cli.render import DEFAULT_LIMIT, render, visible_columns
from labmon.export.table import combine, normalise

runner = CliRunner()


def _run(*args: str) -> Result:
    """Invoke a labmon command, typed so the result's fields resolve."""
    return runner.invoke(build_app(), list(args))


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
            return pa.table(
                {"column_name": pa.array(["time", "value", "sensor_id", "unit"])}
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
    monkeypatch.setattr(selection, "get_client", FakeClient)

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

    monkeypatch.setattr(selection, "get_client", Failing)
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

    monkeypatch.setattr(selection, "get_client", unreachable)
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
    monkeypatch.setattr(selection, "get_client", unreachable)
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

    monkeypatch.setattr(selection, "get_client", explode)
    monkeypatch.setattr("sys.argv", ["labmon", "query"])

    with pytest.raises(KeyError, match="something else entirely"):
        cli_main.main()
