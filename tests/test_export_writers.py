"""Writing the export table out in each format."""

import io
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pyarrow import ipc as pa_ipc

from labmon.export.table import attach_metadata, combine, normalise
from labmon.export.window import Window
from labmon.export.writers import (
    FORMATS,
    STREAMABLE,
    SUFFIXES,
    UNNAMED_PART,
    ExportError,
    safe_filename_part,
    write,
    write_stdout,
)
from tests.support import attr as _attr
from tests.support import open_dataset as _open

_WINDOW = Window(
    since=datetime(2026, 8, 1, tzinfo=UTC), until=datetime(2026, 8, 2, tzinfo=UTC)
)


def _table(units: list[str] | None = None) -> pa.Table:
    raw = pa.table(
        {
            "time": pa.array([0, 1_000_000_000], pa.timestamp("ns")),
            "sensor_id": pa.array(["cryo-77k", "room-1"]),
            "value": pa.array([77.3, 21.4]),
            "unit": pa.array(units or ["K", "degC"]),
        }
    )
    return attach_metadata(combine([normalise(raw, "temperature")]), _WINDOW)


@pytest.mark.parametrize("fmt", FORMATS)
def test_every_format_writes_a_readable_file(fmt: str, tmp_path: Path) -> None:
    target = tmp_path / f"out{SUFFIXES[fmt]}"

    write(_table(), target, fmt)

    assert target.stat().st_size > 0


@pytest.mark.parametrize("fmt", sorted(STREAMABLE))
def test_a_streamable_format_writes_to_a_pipe(fmt: str) -> None:
    sink = io.BytesIO()

    write_stdout(_table(), fmt, sink)

    assert sink.getvalue()


def test_netcdf_cannot_be_streamed() -> None:
    # Both engines seek while writing, which a pipe does not support.
    with pytest.raises(ExportError, match="cannot be written to stdout"):
        write_stdout(_table(), "netcdf", io.BytesIO())


def test_netcdf_to_a_stream_is_refused_with_a_reason() -> None:
    with pytest.raises(ExportError, match="seeks"):
        write(_table(), io.BytesIO(), "netcdf")


def test_an_unknown_format_lists_the_known_ones(tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="unknown format"):
        write(_table(), tmp_path / "out.xlsx", "xlsx")


def test_parquet_keeps_the_dictionary_encoding(tmp_path: Path) -> None:
    target = tmp_path / "out.parquet"

    write(_table(), target, "parquet")

    schema = pq.read_schema(target)
    assert "dictionary" in str(schema.field("unit").type)


def test_parquet_keeps_the_manifest(tmp_path: Path) -> None:
    target = tmp_path / "out.parquet"

    write(_table(), target, "parquet")

    assert b"labmon" in (pq.read_schema(target).metadata or {})


def test_feather_round_trips_the_rows(tmp_path: Path) -> None:
    target = tmp_path / "out.feather"

    write(_table(), target, "feather")

    with target.open("rb") as handle:
        restored = pa_ipc.open_file(handle).read_all()
    assert restored.column("value").to_pylist() == [77.3, 21.4]


def test_feather_keeps_the_field_unit_when_there_is_only_one(tmp_path: Path) -> None:
    target = tmp_path / "out.feather"

    write(_table(units=["K", "K"]), target, "feather")

    with target.open("rb") as handle:
        restored = pa_ipc.open_file(handle).read_all()
    assert restored.schema.field("value").metadata == {b"unit": b"K"}


def test_csv_carries_the_unit_as_a_column(tmp_path: Path) -> None:
    # The column, not only the metadata: pandas and polars both drop Arrow
    # metadata, so a metadata-only unit would be invisible to the readers
    # this exists for.
    target = tmp_path / "out.csv"

    write(_table(), target, "csv")

    text = target.read_text(encoding="utf-8")
    assert "unit" in text.splitlines()[0]
    assert "K" in text


def test_netcdf_gives_each_sensor_its_own_time_axis(tmp_path: Path) -> None:
    target = tmp_path / "out.nc"

    write(_table(), target, "netcdf")

    dataset = _open(target)
    assert set(dataset.sizes) == {"time_cryo-77k", "time_room-1"}


def test_netcdf_records_the_unit_as_a_cf_attribute(tmp_path: Path) -> None:
    target = tmp_path / "out.nc"

    write(_table(), target, "netcdf")

    dataset = _open(target)
    assert _attr(dataset["cryo-77k"], "units") == "K"
    assert _attr(dataset["room-1"], "units") == "degC"


def test_netcdf_stores_every_axis_against_the_unix_epoch(tmp_path: Path) -> None:
    # Left to itself xarray picks each variable's first timestamp, so one
    # file carries several epochs and two files cannot be compared without
    # decoding both.
    target = tmp_path / "out.nc"

    write(_table(), target, "netcdf")

    # decode_times=False keeps the raw CF attribute instead of turning it
    # back into datetimes, which is the thing under test here.
    dataset = _open(target, decode_times=False)
    epochs = {_attr(dataset[name], "units") for name in dataset.coords}
    assert epochs == {"milliseconds since 1970-01-01"}


def test_a_sensorless_row_becomes_one_named_variable(tmp_path: Path) -> None:
    raw = pa.table(
        {
            "time": pa.array([0], pa.timestamp("ns")),
            "value": pa.array([1.0]),
        }
    )
    table = attach_metadata(combine([normalise(raw, "probe")]), _WINDOW)
    target = tmp_path / "out.nc"

    write(table, target, "netcdf")

    assert UNNAMED_PART in _open(target).data_vars


def test_a_sensor_named_unnamed_keeps_its_own_variable(tmp_path: Path) -> None:
    # The stand-in name must be one no sensor can carry, or the two get
    # merged into a single variable without anything saying so.
    raw = pa.table(
        {
            "time": pa.array([0, 1], pa.timestamp("ns")),
            "sensor_id": pa.array(["unnamed", None]),
            "value": pa.array([1.0, 2.0]),
        }
    )
    table = attach_metadata(combine([normalise(raw, "probe")]), _WINDOW)
    target = tmp_path / "out.nc"

    write(table, target, "netcdf")
    dataset = _open(target)

    assert dataset["unnamed"].values.tolist() == [1.0]
    assert dataset[UNNAMED_PART].values.tolist() == [2.0]


@pytest.mark.parametrize("sensor_id", ["cryo-77k", "room_1", "a.b", "A0"])
def test_an_ordinary_sensor_id_is_usable_in_a_filename(sensor_id: str) -> None:
    assert safe_filename_part(sensor_id) == sensor_id


@pytest.mark.parametrize(
    "sensor_id",
    ["../etc/passwd", "a/b", "", "with space", "x" * 65, "naïve"],
)
def test_a_sensor_id_that_would_escape_the_output_directory_is_refused(
    sensor_id: str,
) -> None:
    # A sensor id comes from an operator-edited file, so a `/` or a `..`
    # in one would put the output somewhere nobody asked for.
    with pytest.raises(ExportError, match="cannot be used in a filename"):
        _ = safe_filename_part(sensor_id)
