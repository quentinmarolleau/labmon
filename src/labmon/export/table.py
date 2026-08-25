"""The one table shape every export format is written from.

Readings come back from the server with whatever columns each
measurement happens to have, timestamps in nanoseconds, and tag columns
as plain strings. Every writer wants the same thing instead: one long
table, a fixed column order, and labels that cost nothing to repeat.

The label columns are dictionary-encoded, which is what makes carrying
the unit on every row affordable. Measured over a million rows, adding a
`unit` column costs +0.06% in Parquet and +0.35% in Feather once
dictionary-encoded, against +44% in Feather when left as a plain string
(Arrow IPC does not compress by default, so a repeated string is written
out in full for every row). It also arrives as a `category` in pandas and
a `Categorical` in polars rather than as an object column.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib import metadata
from typing import cast

import pyarrow as pa
import pyarrow.compute as pc

from labmon.export.window import Window

# Column order every export uses, whichever format it lands in. Time
# first, then what the reading is of, then the number, then provenance.
EXPORT_COLUMNS: tuple[str, ...] = (
    "time",
    "sensor_id",
    "measurement",
    "value",
    "unit",
    "input_volts",
    "calibration_id",
)

# Columns whose values repeat heavily and are therefore stored as an
# Arrow dictionary rather than one string per row.
LABEL_COLUMNS: frozenset[str] = frozenset(
    {"sensor_id", "measurement", "unit", "calibration_id"}
)

# Milliseconds, matching the write_precision every sensor writes with.
# Keeping nanoseconds would imply a resolution nothing in the stack has,
# and pandas carried timestamp[ns] as a 64-bit count that overflows
# outside 1677-2262 — a range a lab timestamp will not leave, but the
# narrower type is honest about what was recorded either way.
TIME_TYPE: pa.DataType = pa.timestamp("ms", tz="UTC")

_LABEL_TYPE: pa.DataType = pa.dictionary(pa.int32(), pa.string())

_METADATA_KEY = b"labmon"


def _labmon_version() -> str:
    try:
        return metadata.version("labmon")
    except metadata.PackageNotFoundError:  # pragma: no cover - installed in CI
        return "unknown"


def _utc_time_column(column: pa.ChunkedArray) -> pa.ChunkedArray:
    """Coerce a time column to UTC milliseconds.

    InfluxDB returns `timestamp[ns]` with no timezone. That is UTC — it
    is what was written — but Arrow will not assume so, and casting a
    naive column straight to a tz-aware type stamps the offset on without
    shifting, which happens to be right here and would be silently wrong
    for a column that really was local. `assume_timezone` says which
    reading is intended rather than relying on that.

    The narrowing cast is `safe=False`, which truncates. Arrow's default
    raises instead, so a single stamp not landing on a whole millisecond
    would fail the whole export. Every labmon sensor writes with
    `write_precision="ms"` and cannot produce one, but anything else
    writing to the same database can, and losing sub-millisecond digits
    the stack does not claim to resolve is the intended trade.
    """
    if isinstance(column.type, pa.TimestampType) and column.type.tz is not None:
        return column.cast(TIME_TYPE, safe=False)
    localized = pc.assume_timezone(column, "UTC")
    return localized.cast(TIME_TYPE, safe=False)


def normalise(table: pa.Table, measurement: str) -> pa.Table:
    """Reshape one measurement's rows into the canonical export table.

    Columns the measurement does not have are filled with nulls of the
    right type, so tables with and without `input_volts` concatenate.
    """
    rows = table.num_rows
    present = set(table.column_names)
    columns: list[pa.ChunkedArray] = []

    for name in EXPORT_COLUMNS:
        if name == "measurement":
            column = pa.chunked_array([pa.array([measurement] * rows, pa.string())])
        elif name in present:
            column = table.column(name)
        elif name in LABEL_COLUMNS:
            column = pa.chunked_array([pa.nulls(rows, pa.string())])
        else:
            column = pa.chunked_array([pa.nulls(rows, pa.float64())])

        if name == "time":
            column = _utc_time_column(column)
        elif name in LABEL_COLUMNS:
            column = column.cast(pa.string()).cast(_LABEL_TYPE)
        else:
            column = column.cast(pa.float64())
        columns.append(column)

    return pa.Table.from_arrays(list(columns), names=list(EXPORT_COLUMNS))


def combine(tables: Sequence[pa.Table]) -> pa.Table:
    """Concatenate normalised tables and put them back in time order.

    Each measurement arrives sorted, but a multi-measurement export
    interleaves them, and a reader plotting a monolithic file expects one
    monotonic time axis rather than a sawtooth per measurement.
    """
    if not tables:
        return pa.Table.from_arrays(
            [pa.nulls(0, _column_type(name)) for name in EXPORT_COLUMNS],
            names=list(EXPORT_COLUMNS),
        )
    combined = pa.concat_tables(tables)
    if combined.num_rows == 0:
        return combined
    # Arrow refuses to sort a dictionary column, so the sort keys are
    # taken from a plain-string view and the row order that produces is
    # applied to the real table — which keeps the dictionary encoding
    # that makes the label columns cheap in the first place.
    keys = pa.table(
        {
            "time": combined.column("time"),
            "sensor_id": combined.column("sensor_id").cast(pa.string()),
        }
    )
    order = pc.sort_indices(
        keys, sort_keys=[("time", "ascending"), ("sensor_id", "ascending")]
    )
    return combined.take(order)


def _column_type(name: str) -> pa.DataType:
    if name == "time":
        return TIME_TYPE
    if name in LABEL_COLUMNS:
        return _LABEL_TYPE
    return pa.float64()


# Columns that record where a reading came from rather than what it is.
# Dropped together by `without_raw_input`: `calibration_id` names the
# conversion that produced `value` from `input_volts`, so one without the
# other says nothing useful.
PROVENANCE_COLUMNS: tuple[str, ...] = ("input_volts", "calibration_id")


def without_raw_input(table: pa.Table) -> pa.Table:
    """Drop the provenance columns, for an export that only wants readings.

    A deliberate loss rather than a tidy-up. `input_volts` is stored so a
    reading can be recomputed when a calibration turns out to be wrong;
    without it, what was recorded is all there is. The unit stays, always
    — a column of bare numbers nobody can interpret is the failure the
    whole calibration layer exists to prevent.

    Dropping columns that are already absent is not an error, so this is
    safe on a measurement written by something other than labmon.
    """
    keep = [name for name in table.column_names if name not in PROVENANCE_COLUMNS]
    return table.select(keep)


def units_by_sensor(table: pa.Table) -> dict[str, list[str]]:
    """The units each sensor in `table` reports, for metadata and netCDF.

    A sensor recalibrated mid-window into a different unit keeps both,
    in the order first seen, rather than silently reporting only the
    last one — that is a situation somebody needs to see, not one to
    tidy away. Kept as a list so that a caller counting distinct units
    counts units rather than counting joined strings; `unit_label`
    joins them at the point of display.
    """
    if table.num_rows == 0:
        return {}
    pairs = table.select(["sensor_id", "unit"]).to_pydict()
    seen: dict[str, list[str]] = {}
    sensors = pairs["sensor_id"]
    units = pairs["unit"]
    for sensor, unit in zip(sensors, units, strict=True):
        if sensor is None or unit is None:
            continue
        bucket = seen.setdefault(str(sensor), [])
        if str(unit) not in bucket:
            bucket.append(str(unit))
    return seen


def unit_label(units: list[str]) -> str:
    """The units of one sensor as a single string, for a human to read."""
    return ", ".join(units)


def attach_metadata(table: pa.Table, window: Window) -> pa.Table:
    """Record what this export is, in the schema itself.

    Parquet and Feather both round-trip Arrow schema and field metadata,
    so this survives the file. pandas and polars ignore it — which is
    exactly why the unit is *also* a column, and not only here.
    """
    units = units_by_sensor(table)
    manifest = {
        "labmon_version": _labmon_version(),
        "exported_at": datetime.now(UTC).isoformat(),
        "window_since": window.since.isoformat(),
        "window_until": window.until.isoformat(),
        "rows": table.num_rows,
        "units": {sensor: unit_label(found) for sensor, found in units.items()},
    }
    # Every unit any sensor reported, counted as units rather than as
    # joined strings: one sensor recalibrated from K to degC is two, and
    # a field-level label would be wrong for half its rows.
    distinct = {unit for found in units.values() for unit in found}

    fields: list[pa.Field] = []
    for field in table.schema:
        if field.name == "value" and len(distinct) == 1:
            # Only when the whole file is one unit: a field-level "unit"
            # on a mixed table would be read as covering every row.
            fields.append(field.with_metadata({b"unit": next(iter(distinct)).encode()}))
        else:
            fields.append(field)

    schema = pa.schema(fields).with_metadata(
        {_METADATA_KEY: json.dumps(manifest, sort_keys=True).encode()}
    )
    return table.cast(schema)


def read_metadata(schema: pa.Schema) -> dict[str, object]:
    """The manifest `attach_metadata` wrote, or an empty dict."""
    raw = (schema.metadata or {}).get(_METADATA_KEY)
    if raw is None:
        return {}
    return cast(dict[str, object], json.loads(raw.decode()))
