"""Reshaping query results into the one table every writer takes."""

from datetime import UTC, datetime

import pyarrow as pa

from labmon.export.table import (
    EXPORT_COLUMNS,
    LABEL_COLUMNS,
    TIME_TYPE,
    attach_metadata,
    combine,
    normalise,
    read_metadata,
    units_by_sensor,
)
from labmon.export.window import Window

_WINDOW = Window(
    since=datetime(2026, 8, 1, tzinfo=UTC), until=datetime(2026, 8, 2, tzinfo=UTC)
)


def _influx_like(
    times: list[int],
    sensors: list[str],
    values: list[float],
    units: list[str] | None = None,
    input_volts: list[float] | None = None,
) -> pa.Table:
    """What a query comes back as: nanoseconds, no timezone, plain strings."""
    columns = {
        "time": pa.array(times, pa.timestamp("ns")),
        "sensor_id": pa.array(sensors),
        "value": pa.array(values),
    }
    if units is not None:
        columns["unit"] = pa.array(units)
    if input_volts is not None:
        columns["input_volts"] = pa.array(input_volts)
    return pa.table(columns)


_ONE_SECOND = 1_000_000_000


def test_every_export_has_the_same_columns_in_the_same_order() -> None:
    table = normalise(_influx_like([0], ["a"], [1.0]), "temperature")

    assert table.column_names == list(EXPORT_COLUMNS)


def test_timestamps_are_utc_milliseconds() -> None:
    # labmon writes at millisecond precision, so nanoseconds would imply a
    # resolution nothing in the stack has.
    table = normalise(_influx_like([1_500_000], ["a"], [1.0]), "temperature")

    assert table.schema.field("time").type == TIME_TYPE


def test_a_naive_timestamp_is_read_as_utc_without_shifting() -> None:
    stamp = int(datetime(2026, 8, 1, 12, 0, tzinfo=UTC).timestamp()) * _ONE_SECOND

    table = normalise(_influx_like([stamp], ["a"], [1.0]), "temperature")

    assert table.column("time").to_pylist() == [datetime(2026, 8, 1, 12, 0, tzinfo=UTC)]


def test_a_timestamp_that_already_has_a_zone_is_converted_not_re_stamped() -> None:
    aware = pa.table(
        {
            "time": pa.array([0], pa.timestamp("ns", tz="+02:00")),
            "sensor_id": pa.array(["a"]),
            "value": pa.array([1.0]),
        }
    )

    table = normalise(aware, "temperature")

    assert table.column("time").to_pylist() == [datetime(1970, 1, 1, tzinfo=UTC)]


def test_sub_millisecond_precision_is_truncated_not_rejected() -> None:
    table = normalise(_influx_like([1_999_999], ["a"], [1.0]), "temperature")

    assert table.column("time").to_pylist() == [
        datetime(1970, 1, 1, 0, 0, 0, 1000, tzinfo=UTC)
    ]


def test_the_measurement_becomes_a_column() -> None:
    # It is the table name in InfluxDB, so it is not in the rows; without
    # this a monolithic export could not tell two measurements apart.
    table = normalise(_influx_like([0, 1], ["a", "b"], [1.0, 2.0]), "pressure")

    assert table.column("measurement").to_pylist() == ["pressure", "pressure"]


def test_label_columns_are_dictionary_encoded() -> None:
    # This is what makes carrying the unit on every row affordable, and
    # what makes it a category in pandas rather than an object column.
    table = normalise(_influx_like([0], ["a"], [1.0], ["K"]), "temperature")

    for name in LABEL_COLUMNS:
        assert isinstance(table.schema.field(name).type, pa.DataType)
        assert "dictionary" in str(table.schema.field(name).type)


def test_a_missing_column_is_filled_with_nulls_of_the_right_type() -> None:
    # So a table that stores input_volts and one that does not can still
    # be concatenated into a single export.
    table = normalise(_influx_like([0], ["a"], [1.0]), "temperature")

    assert table.column("input_volts").to_pylist() == [None]
    assert table.column("unit").to_pylist() == [None]


def test_tables_with_different_columns_concatenate() -> None:
    with_volts = normalise(_influx_like([0], ["a"], [1.0], ["K"], [0.5]), "temperature")
    without = normalise(_influx_like([1], ["b"], [2.0]), "pressure")

    combined = combine([with_volts, without])

    assert combined.num_rows == 2
    assert combined.column("input_volts").to_pylist() == [0.5, None]


def test_a_multi_measurement_export_is_in_time_order() -> None:
    # Each measurement arrives sorted, but interleaving them gives a
    # sawtooth unless the combined table is put back in order.
    first = normalise(_influx_like([0, 2 * _ONE_SECOND], ["a", "a"], [1.0, 3.0]), "t")
    second = normalise(_influx_like([_ONE_SECOND], ["b"], [2.0]), "p")

    combined = combine([first, second])

    assert combined.column("value").to_pylist() == [1.0, 2.0, 3.0]


def test_sorting_keeps_the_dictionary_encoding() -> None:
    # Arrow refuses to sort a dictionary column, so the sort runs on a
    # string view and the order is applied with take(); the cheap
    # encoding has to survive that.
    combined = combine([normalise(_influx_like([1, 0], ["b", "a"], [2.0, 1.0]), "t")])

    assert "dictionary" in str(combined.schema.field("sensor_id").type)


def test_rows_at_the_same_instant_are_ordered_by_sensor() -> None:
    table = normalise(_influx_like([0, 0], ["b", "a"], [2.0, 1.0]), "t")

    combined = combine([table])

    assert combined.column("sensor_id").to_pylist() == ["a", "b"]


def test_combining_nothing_still_gives_the_export_schema() -> None:
    # A window that matched no measurement should produce a valid empty
    # file, not a file with no columns that nothing can read.
    empty = combine([])

    assert empty.column_names == list(EXPORT_COLUMNS)
    assert empty.num_rows == 0


def test_combining_empty_tables_gives_an_empty_table() -> None:
    empty = normalise(_influx_like([], [], []), "temperature")

    assert combine([empty]).num_rows == 0


def test_units_are_collected_per_sensor() -> None:
    table = normalise(
        _influx_like([0, 1, 2], ["a", "b", "a"], [1.0, 2.0, 3.0], ["K", "mbar", "K"]),
        "t",
    )

    assert units_by_sensor(table) == {"a": "K", "b": "mbar"}


def test_a_sensor_recalibrated_into_a_new_unit_reports_both() -> None:
    # Silently reporting only the last one would hide exactly the
    # situation somebody needs to see.
    table = normalise(_influx_like([0, 1], ["a", "a"], [1.0, 2.0], ["K", "degC"]), "t")

    assert units_by_sensor(table) == {"a": "K, degC"}


def test_rows_without_a_unit_are_skipped() -> None:
    table = normalise(_influx_like([0], ["a"], [1.0]), "t")

    assert units_by_sensor(table) == {}


def test_units_of_an_empty_table_are_empty() -> None:
    assert units_by_sensor(combine([])) == {}


def test_the_manifest_records_the_window_and_the_units() -> None:
    table = attach_metadata(
        combine([normalise(_influx_like([0], ["a"], [1.0], ["K"]), "t")]), _WINDOW
    )

    manifest = read_metadata(table.schema)

    assert manifest["window_since"] == "2026-08-01T00:00:00+00:00"
    assert manifest["window_until"] == "2026-08-02T00:00:00+00:00"
    assert manifest["units"] == {"a": "K"}
    assert manifest["rows"] == 1


def test_a_single_unit_export_labels_the_value_column() -> None:
    table = attach_metadata(
        combine([normalise(_influx_like([0], ["a"], [1.0], ["K"]), "t")]), _WINDOW
    )

    assert table.schema.field("value").metadata == {b"unit": b"K"}


def test_a_mixed_unit_export_does_not_label_the_value_column() -> None:
    # A field-level unit on a mixed table would be read as covering every
    # row, which is worse than saying nothing.
    table = attach_metadata(
        combine(
            [
                normalise(
                    _influx_like([0, 1], ["a", "b"], [1.0, 2.0], ["K", "mbar"]), "t"
                )
            ]
        ),
        _WINDOW,
    )

    assert table.schema.field("value").metadata is None


def test_metadata_of_a_plain_table_is_empty() -> None:
    assert read_metadata(pa.table({"x": pa.array([1])}).schema) == {}
