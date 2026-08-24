"""What an exported file looks like once it reaches a notebook.

The point of the export is that somebody opens it in pandas, polars or
xarray. These assert the properties that make that pleasant rather than
merely possible: the unit is visible, the labels are categorical rather
than repeated strings, and the timestamps carry the millisecond
resolution the stack actually records.

They also pin the reason the unit is a column and not only metadata:
both libraries drop Arrow schema metadata on read, so a metadata-only
unit would be invisible to every reader this feature exists for.
"""

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from labmon.export.table import attach_metadata, combine, normalise
from labmon.export.window import Window
from labmon.export.writers import write

_WINDOW = Window(
    since=datetime(2026, 8, 1, tzinfo=UTC), until=datetime(2026, 8, 2, tzinfo=UTC)
)


@pytest.fixture
def exported(tmp_path: Path) -> Path:
    raw = pa.table(
        {
            # 1.5 s past the epoch: a whole number of milliseconds that is
            # not a whole number of seconds, so a writer that silently
            # rounded to seconds would be caught.
            "time": pa.array([1_500_000_000, 2_500_000_000], pa.timestamp("ns")),
            "sensor_id": pa.array(["cryo-77k", "room-1"]),
            "value": pa.array([77.3, 21.4]),
            "unit": pa.array(["K", "degC"]),
        }
    )
    table = attach_metadata(combine([normalise(raw, "temperature")]), _WINDOW)
    target = tmp_path / "run.parquet"
    write(table, target, "parquet")
    write(table, tmp_path / "run.feather", "feather")
    write(table, tmp_path / "run.csv", "csv")
    return target


def test_pandas_sees_the_unit_as_a_column(exported: Path) -> None:
    frame = pd.read_parquet(exported)

    assert frame.loc[frame["sensor_id"] == "cryo-77k", "unit"].tolist() == ["K"]


def test_pandas_reads_labels_as_categories(exported: Path) -> None:
    # Dictionary-encoded rather than one string per row, which is what
    # makes carrying the unit on every row cost nothing.
    frame = pd.read_parquet(exported)

    for column in ("sensor_id", "measurement", "unit"):
        assert str(frame[column].dtype) == "category"


def test_pandas_keeps_millisecond_timestamps(exported: Path) -> None:
    frame = pd.read_parquet(exported)

    assert str(frame["time"].dtype) == "datetime64[ms, UTC]"
    assert frame["time"].iloc[0] == pd.Timestamp("1970-01-01 00:00:01.500", tz="UTC")


def test_pandas_drops_arrow_metadata(exported: Path) -> None:
    # The reason the unit is a column. This is a property of pandas, not
    # of the export: if a future pandas starts surfacing it, this test
    # failing is the signal to revisit, not a regression.
    assert b"labmon" in (pq.read_schema(exported).metadata or {})

    assert pd.read_parquet(exported).attrs == {}


def test_polars_reads_labels_as_categoricals(exported: Path) -> None:
    frame = pl.read_parquet(exported)

    assert frame.schema["unit"] == pl.Categorical
    assert frame.schema["sensor_id"] == pl.Categorical


def test_polars_keeps_millisecond_timestamps(exported: Path) -> None:
    frame = pl.read_parquet(exported)

    assert frame.schema["time"] == pl.Datetime(time_unit="ms", time_zone="UTC")


def test_polars_reads_the_feather_file(exported: Path) -> None:
    frame = pl.read_ipc(exported.with_name("run.feather"))

    assert frame["value"].to_list() == [77.3, 21.4]
    assert frame.schema["unit"] == pl.Categorical


def test_the_csv_parses_without_being_told_a_date_format(exported: Path) -> None:
    # pyarrow writes "1970-01-01 00:00:01.500Z", which both libraries
    # recognise unaided; a format nobody can parse would make the CSV
    # useless for exactly the audience it is the default for.
    target = exported.with_name("run.csv")

    from_pandas = pd.read_csv(target, parse_dates=["time"])
    from_polars = pl.read_csv(target, try_parse_dates=True)

    assert str(from_pandas["time"].dtype).startswith("datetime64")
    assert from_polars.schema["time"] == pl.Datetime(time_unit="us", time_zone="UTC")


def test_a_long_export_pivots_to_a_wide_table_in_one_call(exported: Path) -> None:
    # Long format is the default because it survives mixed sample rates.
    # This is the escape hatch that makes that choice cheap.
    frame = pd.read_parquet(exported)

    wide = frame.pivot(index="time", columns="sensor_id", values="value")

    assert sorted(str(name) for name in wide.columns) == ["cryo-77k", "room-1"]
