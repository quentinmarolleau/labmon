"""Writing the canonical export table out in each supported format.

CSV, Parquet and Feather cost nothing to support: `pyarrow` is already a
hard dependency of the InfluxDB client. netCDF is not — it needs
`xarray`, so it lives behind the `netcdf` extra and reports that clearly
rather than failing on an import trace.
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, cast

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
from pyarrow import ipc as pa_ipc

from labmon.export.formats import (
    FORMATS,
    STREAMABLE,
    SUFFIXES,
    ExportError,
)
from labmon.export.table import read_metadata, units_by_sensor

if TYPE_CHECKING:
    # Only for the annotation: xarray lives behind the `netcdf` extra, so
    # importing it at module scope would make a CSV export fail on a host
    # that deliberately did not install it.
    import xarray as xr

logger: logging.Logger = logging.getLogger(__name__)

__all__ = [
    "FORMATS",
    "STREAMABLE",
    "SUFFIXES",
    "ExportError",
    "safe_filename_part",
    "write",
    "write_stdout",
]


# What a sensor id may contain before it is allowed to become part of a
# filename. Deliberately narrow: a sensor id is operator-supplied, and a
# `/` or a `..` in one would place the output somewhere nobody asked for.
_SAFE_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")


def safe_filename_part(sensor_id: str) -> str:
    """Check a sensor id is usable in a filename, or say why it is not."""
    if not _SAFE_NAME.match(sensor_id):
        raise ExportError(
            f"sensor id {sensor_id!r} cannot be used in a filename;"
            + " --split-per-sensor needs ids of at most 64 characters made of"
            + " letters, digits, dot, dash or underscore"
        )
    return sensor_id


def _write_csv(table: pa.Table, sink: BinaryIO) -> None:
    # Dictionary columns and null columns both write fine; timestamps come
    # out as "2026-08-01 12:00:00.000Z", which pandas and polars both
    # parse without being told a format.
    pacsv.write_csv(table, sink)


def _write_parquet(table: pa.Table, sink: BinaryIO) -> None:
    # store_schema keeps the ARROW:schema key, which is what carries the
    # dictionary types and the field-level unit back to a pyarrow reader.
    pq.write_table(table, sink, compression="zstd", store_schema=True)


def _write_feather(table: pa.Table, sink: BinaryIO) -> None:
    """Write Feather V2, which is the Arrow IPC file format.

    Built through `pa.ipc.new_file` rather than `feather.write_feather`,
    which pyarrow deprecated in 24.0.0 and which emits a FutureWarning
    into the caller's stderr on every export.

    Compression is worth setting explicitly: Arrow IPC does not compress
    by default, and these files are mostly repeated labels and doubles.
    """
    options = pa_ipc.IpcWriteOptions(compression="zstd")
    with pa_ipc.new_file(sink, table.schema, options=options) as writer:
        writer.write_table(table)


# Suffix for the companion variable holding a sensor's ADC input.
INPUT_VOLTS_SUFFIX = "_input_volts"


@dataclass
class _Channel:
    """One sensor's readings while a netCDF Dataset is being built."""

    times: list[object] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    volts: list[float | None] = field(default_factory=list)
    calibrations: list[str] = field(default_factory=list)


def _netcdf_dataset(table: pa.Table) -> "xr.Dataset":
    """Build an xarray Dataset with one variable per sensor.

    Each sensor keeps its own time dimension. Aligning them onto a shared
    axis is the obvious alternative and a bad one: a 1 Hz and a 0.2 Hz
    sensor merged on the union of their timestamps is 80% padding, and
    picking a resample rule instead would invent data the database does
    not hold.
    """
    try:
        import numpy as np
        import xarray as xr
    except ModuleNotFoundError as error:  # pragma: no cover - exercised by CI extra
        raise ExportError(
            "netCDF export needs xarray and a netCDF engine."
            + " Install them with: pip install 'labmon[netcdf]'"
        ) from error

    units = units_by_sensor(table)
    manifest = read_metadata(table.schema)
    rows = table.to_pydict()

    grouped: dict[str, _Channel] = {}
    order: list[str] = []
    sensor_column = rows["sensor_id"]
    time_column = rows["time"]
    value_column = cast(list[float], rows["value"])
    volts_column = cast(list[float | None], rows.get("input_volts") or [])
    calibration_column = rows.get("calibration_id") or []
    for index, sensor in enumerate(sensor_column):
        name = "unnamed" if sensor is None else str(sensor)
        if name not in grouped:
            grouped[name] = _Channel()
            order.append(name)
        channel = grouped[name]
        channel.times.append(time_column[index])
        channel.values.append(value_column[index])
        channel.volts.append(volts_column[index] if index < len(volts_column) else None)
        if index < len(calibration_column):
            identifier = calibration_column[index]
            if identifier is not None and str(identifier) not in channel.calibrations:
                channel.calibrations.append(str(identifier))

    variables: dict[str, xr.DataArray] = {}
    for name in order:
        channel = grouped[name]
        dim = f"time_{name}"
        # numpy has no timezone-aware datetime64 and warns when it drops
        # one. Every stamp here is already UTC, so the offset is stripped
        # deliberately rather than left to a warning on every export; the
        # epoch written into the file records that it is UTC.
        naive = [
            stamp.replace(tzinfo=None) if isinstance(stamp, datetime) else stamp
            for stamp in channel.times
        ]
        axis = np.asarray(naive, dtype="datetime64[ms]")
        reading_attrs: dict[str, str] = {
            "units": units.get(name, ""),
            "long_name": name,
        }
        if channel.calibrations:
            # Which calibration turned the voltage into this number.
            # Joined when a sensor was recalibrated mid-window: reporting
            # only the last one would hide the very thing somebody needs
            # to see.
            reading_attrs["calibration_id"] = ", ".join(channel.calibrations)

        if any(volt is not None for volt in channel.volts):
            # The voltage the reading was converted from, on the same
            # axis as the reading: same sample, same instant. Omitted
            # entirely for a sensor that never had one, because an
            # all-NaN variable suggests a measurement that was attempted
            # and failed.
            companion = f"{name}{INPUT_VOLTS_SUFFIX}"
            variables[companion] = xr.DataArray(
                np.asarray(
                    [np.nan if volt is None else volt for volt in channel.volts],
                    dtype="float64",
                ),
                dims=[dim],
                coords={dim: axis},
                attrs={"units": "V", "long_name": f"{name} ADC input"},
            )
            # CF's way of saying "this other variable describes this
            # one", so a reader finds the voltage without having to guess
            # the naming scheme.
            reading_attrs["ancillary_variables"] = companion

        variables[name] = xr.DataArray(
            np.asarray(channel.values, dtype="float64"),
            dims=[dim],
            coords={dim: axis},
            attrs=reading_attrs,
        )

    attrs: dict[str, str] = {"Conventions": "CF-1.10", "title": "labmon export"}
    attrs.update(
        {
            key: json.dumps(value) if isinstance(value, dict) else str(value)
            for key, value in manifest.items()
        }
    )
    return xr.Dataset(variables, attrs=attrs)


# Every time coordinate is stored against this epoch. Left to itself
# xarray picks the first timestamp of each variable, so a file with five
# sensors carries five different epochs and two files are not comparable
# without decoding both. Milliseconds, matching what sensors record.
NETCDF_TIME_UNITS = "milliseconds since 1970-01-01T00:00:00"


def _write_netcdf(table: pa.Table, path: Path) -> None:
    dataset = _netcdf_dataset(table)
    encoding: dict[str, dict[str, str]] = {
        str(name): {"units": NETCDF_TIME_UNITS, "dtype": "int64"}
        for name in dataset.coords
    }
    # to_netcdf seeks, so it takes a path rather than the shared sink.
    # xarray overloads to_netcdf on the path argument; with a real path it
    # returns None, which pyright cannot narrow through the overload set.
    _ = dataset.to_netcdf(path, encoding=encoding)  # pyright: ignore[reportUnknownMemberType]


_STREAM_WRITERS: dict[str, Callable[[pa.Table, BinaryIO], None]] = {
    "csv": _write_csv,
    "parquet": _write_parquet,
    "feather": _write_feather,
}


def write(table: pa.Table, destination: Path | BinaryIO, fmt: str) -> None:
    """Write `table` as `fmt` to a path or an already-open binary stream."""
    if fmt not in SUFFIXES:
        raise ExportError(f"unknown format {fmt!r}; choose one of {', '.join(FORMATS)}")

    if fmt == "netcdf":
        if not isinstance(destination, Path):
            raise ExportError(
                "netCDF cannot be written to a stream, because its writer"
                + " seeks; give -o a filename instead of '-'"
            )
        _write_netcdf(table, destination)
        return

    writer = _STREAM_WRITERS[fmt]
    if isinstance(destination, Path):
        with destination.open("wb") as handle:
            writer(table, handle)
    else:
        writer(table, destination)


def write_stdout(table: pa.Table, fmt: str, stdout: BinaryIO) -> None:
    """Write to a pipe, refusing the formats that cannot be streamed."""
    if fmt not in STREAMABLE:
        raise ExportError(
            f"{fmt} cannot be written to stdout; give -o a filename instead"
        )
    write(table, stdout, fmt)
