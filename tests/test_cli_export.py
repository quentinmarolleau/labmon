"""The `labmon export` command."""

from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner, Result

from labmon.cli.commands import export as export_cmd
from labmon.cli.main import build_app
from labmon.cli.options import Format
from labmon.export.table import attach_metadata, combine, normalise
from labmon.export.window import Window

runner = CliRunner()

_WINDOW = Window(
    since=datetime(2026, 8, 1, tzinfo=UTC), until=datetime(2026, 8, 2, tzinfo=UTC)
)


class FakeClient:
    """Serves one measurement of two sensors, without a server."""

    def __init__(self) -> None:
        self.closed: bool = False

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
                {
                    "column_name": pa.array(
                        ["time", "value", "sensor_id", "unit", "calibration_id"]
                    )
                }
            )
        return pa.table(
            {
                "time": pa.array([0, 1_000_000_000], pa.timestamp("ns")),
                "value": pa.array([77.3, 21.4]),
                "sensor_id": pa.array(["cryo-77k", "room-1"]),
                "unit": pa.array(["K", "degC"]),
                "calibration_id": pa.array(["abc", "def"]),
            }
        )

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    client = FakeClient()
    monkeypatch.setattr("labmon.influx.get_client", lambda: client)
    return client


def _run(*args: str) -> Result:
    """Invoke `labmon export`, typed so the result's fields resolve."""
    return runner.invoke(build_app(), ["export", *args])


# --------------------------------------------------------------------------
# Choosing the format and the filename


@pytest.mark.parametrize(
    ("output", "requested", "expected"),
    [
        ("run.parquet", None, "parquet"),
        ("run.feather", None, "feather"),
        ("run.nc", None, "netcdf"),
        ("run.csv", None, "csv"),
        ("run.PARQUET", None, "parquet"),
        ("run.txt", None, "csv"),
        (None, None, "csv"),
        ("-", None, "csv"),
        ("run.csv", Format.parquet, "parquet"),
    ],
)
def test_the_format_follows_the_filename_unless_it_is_given(
    output: str | None, requested: Format | None, expected: str
) -> None:
    assert export_cmd.infer_format(output, requested) == expected


@pytest.mark.parametrize(
    ("output", "fmt", "expected"),
    [
        ("test", "feather", "test.feather"),
        ("test", "csv", "test.csv"),
        ("run.feather", "feather", "run.feather"),
        ("run.FEATHER", "feather", "run.FEATHER"),
        ("run.2026-08-24", "csv", "run.2026-08-24.csv"),
        ("archive/run", "parquet", "archive/run.parquet"),
    ],
)
def test_the_format_extension_is_appended_when_missing(
    output: str, fmt: str, expected: str
) -> None:
    assert str(export_cmd.with_suffix(output, fmt)) == expected


def test_a_contradictory_extension_is_left_as_typed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # -o run.csv --format parquet contradicts itself. The explicit flag
    # decides the contents; renaming the explicit filename as well would
    # be the surprising half of that, so it warns instead.
    with caplog.at_level("WARNING"):
        result = export_cmd.with_suffix("run.csv", "parquet")

    assert str(result) == "run.csv"
    assert "another format's extension" in caplog.text


@pytest.mark.usefixtures("fake_client")
def test_a_default_filename_is_invented_per_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = _run("--format", "parquet")

    assert result.exit_code == 0, result.output
    assert (tmp_path / "labmon-export.parquet").exists()


# --------------------------------------------------------------------------
# Writing


@pytest.mark.usefixtures("fake_client")
def test_a_monolithic_export_holds_every_sensor(tmp_path: Path) -> None:
    target = tmp_path / "run.csv"

    _ = _run("-o", str(target))

    text = target.read_text(encoding="utf-8")
    assert "cryo-77k" in text
    assert "room-1" in text


@pytest.mark.usefixtures("fake_client")
def test_splitting_writes_one_file_per_sensor(tmp_path: Path) -> None:
    target = tmp_path / "run.csv"

    _ = _run("-o", str(target), "--split-per-sensor")

    assert (tmp_path / "run_cryo-77k.csv").exists()
    assert (tmp_path / "run_room-1.csv").exists()
    assert not target.exists()


@pytest.mark.usefixtures("fake_client")
def test_each_split_file_holds_only_its_own_sensor(tmp_path: Path) -> None:
    _ = _run("-o", str(tmp_path / "run.csv"), "--split-per-sensor")

    text = (tmp_path / "run_cryo-77k.csv").read_text(encoding="utf-8")
    assert "room-1" not in text


@pytest.mark.usefixtures("fake_client")
def test_a_split_file_carries_its_own_single_unit_metadata(tmp_path: Path) -> None:
    # The whole point of splitting is that each file is one sensor, so
    # each one can carry the field-level unit a mixed file cannot.
    _ = _run("-o", str(tmp_path / "run.parquet"), "--split-per-sensor")

    schema = pq.read_schema(tmp_path / "run_cryo-77k.parquet")
    assert schema.field("value").metadata == {b"unit": b"K"}


@pytest.mark.usefixtures("fake_client")
def test_a_split_export_without_a_suffix_still_names_its_parts(
    tmp_path: Path,
) -> None:
    _ = _run("-o", str(tmp_path / "run"), "--format", "csv", "--split-per-sensor")

    assert (tmp_path / "run_cryo-77k.csv").exists()


@pytest.mark.usefixtures("fake_client")
def test_splitting_to_stdout_is_refused() -> None:
    result = _run("-o", "-", "--split-per-sensor")

    assert result.exit_code == 2


@pytest.mark.usefixtures("fake_client")
def test_stdout_receives_the_file() -> None:
    result = _run("-o", "-")

    assert result.exit_code == 0
    assert "cryo-77k" in result.output


# --------------------------------------------------------------------------
# Nothing to export


class Empty(FakeClient):
    @override
    def query(
        self,
        query: str,
        language: str = "sql",
        mode: str = "all",
        database: str | None = None,
        **kwargs: object,
    ) -> pa.Table:
        table = super().query(query, language, mode, database, **kwargs)
        if "SHOW TABLES" in query or "information_schema" in query:
            return table
        return table.slice(0, 0)


def test_an_empty_result_still_writes_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A window that matched nothing is an answer, and a script checking
    # for the file should find one rather than an absence.
    monkeypatch.setattr("labmon.influx.get_client", Empty)
    target = tmp_path / "run.csv"

    _ = _run("-o", str(target))

    assert target.exists()


def test_an_empty_result_warns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Read the runner's stderr rather than caplog: the command calls
    # logs.configure(), which passes force=True and therefore removes the
    # handler caplog installs. stderr is also where an operator sees it.
    monkeypatch.setattr("labmon.influx.get_client", Empty)

    result = _run("-o", str(tmp_path / "run.csv"))

    assert "no readings matched" in result.stderr


def test_a_sensorless_row_becomes_its_own_part() -> None:
    parts = export_cmd.split_tables(
        attach_metadata(
            combine(
                [
                    normalise(
                        pa.table(
                            {
                                "time": pa.array([0], pa.timestamp("ns")),
                                "value": pa.array([1.0]),
                            }
                        ),
                        "probe",
                    )
                ]
            ),
            _WINDOW,
        )
    )

    assert [name for name, _ in parts] == ["unnamed"]


# --------------------------------------------------------------------------
# Failures


def test_the_client_is_closed_even_when_the_query_fails(
    fake_client: FakeClient,
) -> None:
    # A CLI that leaks the connection on the error path leaves the server
    # holding it until the process exits.
    result = _run("--measurement", "nope")

    assert result.exit_code == 2
    assert fake_client.closed


@pytest.mark.usefixtures("fake_client")
def test_a_refused_request_exits_two_without_a_traceback() -> None:
    result = _run("--since", "nonsense")

    assert result.exit_code == 2
    assert "Traceback" not in result.output


@pytest.mark.usefixtures("fake_client")
def test_a_successful_run_exits_zero(tmp_path: Path) -> None:
    result = _run("-o", str(tmp_path / "run.csv"))

    assert result.exit_code == 0, result.output


@pytest.mark.usefixtures("fake_client")
def test_the_documented_invocation_is_accepted(tmp_path: Path) -> None:
    result = _run(
        "--sensor-id",
        "cryo-77k",
        "--sensor-id",
        "room-1",
        "--measurement",
        "temperature",
        "--since",
        "2026-08-01",
        "--until",
        "2026-08-02",
        "--format",
        "parquet",
        "-o",
        str(tmp_path / "run.parquet"),
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "run.parquet").exists()
