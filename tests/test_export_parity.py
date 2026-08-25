"""What survives a round trip, in every format.

The netCDF writer once carried only `value`, so an export to netCDF
silently lost `input_volts` and `calibration_id` while the other three
kept them. Nothing caught it: the tests asserted that variables and
their units existed, never that the columns did.

These compare formats against each other rather than against a fixed
expectation, so a format that starts dropping something fails here even
if nobody thought to assert on that column.
"""

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pyarrow import ipc as pa_ipc

from labmon.export.table import (
    attach_metadata,
    combine,
    normalise,
    without_raw_input,
)
from labmon.export.window import Window
from labmon.export.writers import write
from tests.support import attr, has_attr, open_dataset

_WINDOW = Window(
    since=datetime(2026, 8, 1, tzinfo=UTC), until=datetime(2026, 8, 2, tzinfo=UTC)
)


@pytest.fixture
def readings() -> pa.Table:
    """Two calibrated channels and one simulated sensor.

    The calibrated pair carry `input_volts` and a `calibration_id`; the
    simulated one carries neither, which is the mix a real export has.
    """
    calibrated = pa.table(
        {
            "time": pa.array([0, 1_000_000_000], pa.timestamp("ns")),
            "sensor_id": pa.array(["cryo-diode", "cryo-diode"]),
            "value": pa.array([77.31, 77.29]),
            "unit": pa.array(["K", "K"]),
            "input_volts": pa.array([1.171, 1.172]),
            "calibration_id": pa.array(["4217920e", "4217920e"]),
        }
    )
    simulated = pa.table(
        {
            "time": pa.array([0, 1_000_000_000], pa.timestamp("ns")),
            "sensor_id": pa.array(["room-1", "room-1"]),
            "value": pa.array([21.4, 21.5]),
            "unit": pa.array(["degC", "degC"]),
        }
    )
    return attach_metadata(
        combine(
            [normalise(calibrated, "temperature"), normalise(simulated, "temperature")]
        ),
        _WINDOW,
    )


def test_parquet_keeps_the_raw_input(readings: pa.Table, tmp_path: Path) -> None:
    target = tmp_path / "out.parquet"

    write(readings, target, "parquet")

    restored = pq.read_table(target).to_pydict()
    assert 1.171 in restored["input_volts"]
    assert "4217920e" in restored["calibration_id"]


def test_feather_keeps_the_raw_input(readings: pa.Table, tmp_path: Path) -> None:
    target = tmp_path / "out.feather"

    write(readings, target, "feather")

    with target.open("rb") as handle:
        restored = pa_ipc.open_file(handle).read_all().to_pydict()
    assert 1.171 in restored["input_volts"]
    assert "4217920e" in restored["calibration_id"]


def test_csv_keeps_the_raw_input(readings: pa.Table, tmp_path: Path) -> None:
    target = tmp_path / "out.csv"

    write(readings, target, "csv")

    text = target.read_text(encoding="utf-8")
    assert "1.171" in text
    assert "4217920e" in text


def test_netcdf_keeps_the_voltage_a_reading_was_converted_from(
    readings: pa.Table, tmp_path: Path
) -> None:
    # `input_volts` is stored so a wrong calibration can be corrected
    # after the fact. An export that drops it removes the only way back.
    target = tmp_path / "out.nc"

    write(readings, target, "netcdf")

    dataset = open_dataset(target)
    assert "cryo-diode_input_volts" in dataset.data_vars
    assert list(dataset["cryo-diode_input_volts"].values) == [1.171, 1.172]


def test_netcdf_labels_the_voltage_in_volts(readings: pa.Table, tmp_path: Path) -> None:
    target = tmp_path / "out.nc"

    write(readings, target, "netcdf")

    dataset = open_dataset(target)
    assert attr(dataset["cryo-diode_input_volts"], "units") == "V"


def test_netcdf_shares_the_time_axis_with_its_reading(
    readings: pa.Table, tmp_path: Path
) -> None:
    # Same sample, same instant: a separate axis would imply they were
    # measured independently.
    target = tmp_path / "out.nc"

    write(readings, target, "netcdf")

    dataset = open_dataset(target)
    assert dataset["cryo-diode_input_volts"].dims == dataset["cryo-diode"].dims


def test_netcdf_records_which_calibration_produced_a_reading(
    readings: pa.Table, tmp_path: Path
) -> None:
    target = tmp_path / "out.nc"

    write(readings, target, "netcdf")

    dataset = open_dataset(target)
    assert attr(dataset["cryo-diode"], "calibration_id") == "4217920e"


def test_netcdf_points_a_reading_at_its_ancillary_variable(
    readings: pa.Table, tmp_path: Path
) -> None:
    # The CF way of saying "this other variable describes this one", so a
    # reader finds the voltage without guessing the naming scheme.
    target = tmp_path / "out.nc"

    write(readings, target, "netcdf")

    dataset = open_dataset(target)
    assert (
        attr(dataset["cryo-diode"], "ancillary_variables") == "cryo-diode_input_volts"
    )


def test_a_sensor_without_a_voltage_gets_no_companion_variable(
    readings: pa.Table, tmp_path: Path
) -> None:
    # A simulated sensor never had one; an all-NaN variable would suggest
    # the measurement was attempted and failed.
    target = tmp_path / "out.nc"

    write(readings, target, "netcdf")

    dataset = open_dataset(target)
    assert "room-1_input_volts" not in dataset.data_vars
    assert not has_attr(dataset["room-1"], "calibration_id")


def test_a_recalibrated_sensor_reports_every_calibration_it_used(
    tmp_path: Path,
) -> None:
    # Reporting only the last one would hide exactly the situation
    # somebody needs to see.
    raw = pa.table(
        {
            "time": pa.array([0, 1_000_000_000], pa.timestamp("ns")),
            "sensor_id": pa.array(["cryo-diode", "cryo-diode"]),
            "value": pa.array([77.3, 77.2]),
            "unit": pa.array(["K", "K"]),
            "input_volts": pa.array([1.171, 1.172]),
            "calibration_id": pa.array(["4217920e", "b0b0b0b0"]),
        }
    )
    table = attach_metadata(combine([normalise(raw, "temperature")]), _WINDOW)
    target = tmp_path / "out.nc"

    write(table, target, "netcdf")

    dataset = open_dataset(target)
    assert attr(dataset["cryo-diode"], "calibration_id") == "4217920e, b0b0b0b0"


# --------------------------------------------------------------------------
# Dropping it on request
#
# The provenance columns are the bulk of a narrow export and mean nothing
# to somebody who only wants the readings, so --no-raw-input removes
# them. It is a deliberate loss: without input_volts a reading cannot be
# recomputed if the calibration turns out to be wrong.


def test_dropping_the_raw_input_removes_both_columns(readings: pa.Table) -> None:
    reduced = without_raw_input(readings)

    assert "input_volts" not in reduced.column_names
    assert "calibration_id" not in reduced.column_names


def test_dropping_the_raw_input_keeps_everything_else(readings: pa.Table) -> None:
    reduced = without_raw_input(readings)

    assert reduced.column_names == ["time", "sensor_id", "measurement", "value", "unit"]
    assert reduced.num_rows == readings.num_rows


def test_dropping_the_raw_input_keeps_the_unit(readings: pa.Table) -> None:
    # The one column that must never be optional: a bare number nobody
    # can interpret is the failure the whole units layer exists to stop.
    reduced = without_raw_input(readings)

    assert "K" in reduced.column("unit").to_pylist()


def test_dropping_it_twice_is_harmless(readings: pa.Table) -> None:
    # A table that never had the columns — a measurement written by
    # something other than labmon — must not raise.
    assert without_raw_input(without_raw_input(readings)).num_rows == readings.num_rows


@pytest.mark.parametrize("fmt", ["csv", "parquet", "feather"])
def test_a_reduced_export_carries_no_provenance(
    readings: pa.Table, tmp_path: Path, fmt: str
) -> None:
    target = tmp_path / f"out.{fmt}"

    write(attach_metadata(without_raw_input(readings), _WINDOW), target, fmt)

    blob = target.read_bytes()
    assert b"4217920e" not in blob
    assert b"input_volts" not in blob


def test_a_reduced_netcdf_has_no_companion_variable(
    readings: pa.Table, tmp_path: Path
) -> None:
    target = tmp_path / "out.nc"

    write(attach_metadata(without_raw_input(readings), _WINDOW), target, "netcdf")

    dataset = open_dataset(target)
    assert "cryo-diode_input_volts" not in dataset.data_vars
    assert not has_attr(dataset["cryo-diode"], "calibration_id")
    assert not has_attr(dataset["cryo-diode"], "ancillary_variables")


def test_a_reduced_netcdf_still_carries_the_readings_and_units(
    readings: pa.Table, tmp_path: Path
) -> None:
    target = tmp_path / "out.nc"

    write(attach_metadata(without_raw_input(readings), _WINDOW), target, "netcdf")

    dataset = open_dataset(target)
    assert list(dataset["cryo-diode"].values) == [77.31, 77.29]
    assert attr(dataset["cryo-diode"], "units") == "K"
