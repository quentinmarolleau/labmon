# Reading an export back

An export is only useful if it loads. This page shows the four files
[`export.md`](export.md) writes going into pandas, polars and xarray,
and explains the one place where the formats genuinely disagree: the
shape of the data once it is in memory.

```bash
labmon export --since 1h --format csv     -o data
labmon export --since 1h --format parquet -o data
labmon export --since 1h --format feather -o data
labmon export --since 1h --format netcdf  -o data
```

Three of those four are **long**: one row per reading, carrying its own
`sensor_id`, `measurement`, `value` and `unit`. That is what the
database holds, and it is what survives sensors running at different
rates. netCDF is the exception, for reasons the last section covers.

## pandas

```python
import pandas as pd

df_csv = pd.read_csv("data.csv", parse_dates=["time"])
df_parquet = pd.read_parquet("data.parquet")
df_feather = pd.read_feather("data.feather")
```

`parse_dates` is the only hint needed anywhere: the timestamp is written
as ISO-8601, which pandas and polars both recognise unaided. Parquet and
Feather carry the label columns as `category` rather than one string per
row, so repeating the unit on every reading costs almost nothing — see
[units travel with the readings](export.md#units-travel-with-the-readings).

pandas has no netCDF reader, so that one goes through xarray:

```python
import xarray as xr

df_netcdf = netcdf_to_long(xr.open_dataset("data.nc"))
```

`netcdf_to_long` is given below. Note what it is **not**:
`Dataset.to_dataframe()`. Each sensor in the file has its own time
dimension, and that method builds the cartesian product of every one of
them — twelve axes at once, which numpy refuses outright with
`iterator is too large`.

## polars

```python
import polars as pl

df_csv = pl.read_csv("data.csv", try_parse_dates=True)
df_parquet = pl.read_parquet("data.parquet")
df_feather = pl.read_ipc("data.feather")
```

Feather **is** the Arrow IPC file format, which is why polars spells it
`read_ipc`. The dictionary-encoded label columns arrive as `Categorical`.

For netCDF, convert through pandas:

```python
df_netcdf = pl.from_pandas(netcdf_to_long(xr.open_dataset("data.nc")))
```

## From netCDF back to a long table

```python
INPUT_VOLTS_SUFFIX = "_input_volts"


def netcdf_to_long(dataset):
    """One frame per reading variable, concatenated."""
    frames = []
    for name in map(str, dataset.data_vars):
        if name.endswith(INPUT_VOLTS_SUFFIX):
            continue  # read alongside the reading it describes
        series = dataset[name]
        companion = series.attrs.get("ancillary_variables")
        volts = (
            dataset[companion].values
            if companion and companion in dataset.data_vars
            else pd.NA
        )
        frames.append(
            pd.DataFrame(
                {
                    "time": series[series.dims[0]].values,
                    "sensor_id": name,
                    "measurement": series.attrs.get("measurement", pd.NA),
                    "value": series.values,
                    "unit": series.attrs.get("units", ""),
                    "input_volts": volts,
                    "calibration_id": series.attrs.get("calibration_id", pd.NA),
                }
            )
        )
    return (
        pd.concat(frames, ignore_index=True).sort_values("time").reset_index(drop=True)
    )
```

A calibrated sensor carries two things the long files hold as columns.
`input_volts` is a **companion variable** sharing the reading's time
axis; the file points at it through the CF `ancillary_variables`
attribute, so it is read from there rather than by guessing the name.
`calibration_id` is an attribute on the reading itself. A simulated
sensor has neither and gets nulls, which is what the long files hold for
it too.

## xarray

netCDF is the one format xarray opens natively, and the one where the
unit is a first-class attribute rather than a column:

```python
ds = xr.open_dataset(
    "data.nc",
    decode_times=xr.coders.CFDatetimeCoder(time_unit="ms"),
)
ds["room-1"].attrs["units"]  # '°C'
ds["cryo-77k"].attrs["measurement"]  # 'temperature'
```

The `decode_times` argument is worth passing. CF decoding widens
timestamps to nanoseconds by default, while labmon records to the
millisecond; naming the unit keeps the axis at the resolution actually
written.

The other three arrive as long tables, and rebuilding them into the same
Dataset takes a group-by:

```python
TIME_DTYPE = "datetime64[ms]"


def long_to_ragged(frame):
    """Build the Dataset the netCDF file already holds."""
    arrays = {}
    for sensor, rows in frame.groupby("sensor_id", observed=True):
        name = str(sensor)
        rows = rows.sort_values("time")
        axis = f"time_{name}"

        stamps = pd.DatetimeIndex(rows["time"])
        if stamps.tz is not None:
            stamps = stamps.tz_convert("UTC").tz_localize(None)
        coords = {axis: stamps.to_numpy().astype(TIME_DTYPE)}

        attrs = {"units": rows["unit"].iloc[0], "long_name": name}
        for column in ("measurement", "calibration_id"):
            values = rows[column].dropna()
            if len(values):
                attrs[column] = str(values.iloc[0])

        volts = rows["input_volts"]
        if volts.notna().any():
            companion = f"{name}{INPUT_VOLTS_SUFFIX}"
            arrays[companion] = xr.DataArray(
                volts.to_numpy(dtype=float),
                coords=coords,
                dims=(axis,),
                attrs={"units": "V", "long_name": f"{name} input"},
            )
            attrs["ancillary_variables"] = companion

        arrays[name] = xr.DataArray(
            rows["value"].to_numpy(dtype=float),
            coords=coords,
            dims=(axis,),
            attrs=attrs,
        )
    return xr.Dataset(arrays)
```

Two details that bite otherwise. Parquet and Feather keep the timestamps
timezone-aware, and a timezone-aware column degrades to an array of
Python objects on the way into numpy, which xarray will not accept as a
time axis — hence converting to UTC and dropping the zone. netCDF has no
notion of a zone at all, since CF pins the epoch in the units string
instead, so dropping it is also what makes all four agree.

Given that, every file produces the same thing:

| source | variables | dimensions |
|---|---|---|
| `data.nc` | 18 | 12 |
| `data.csv` | 18 | 12 |
| `data.parquet` | 18 | 12 |
| `data.feather` | 18 | 12 |

## One time axis, or one per sensor

The shape above is a choice, not something the file format imposes — all
four hold the same readings. It is worth knowing what the alternative
costs.

```python
def align_on_shared_time(dataset):
    """Put every sensor on one time axis, knowingly paying for it."""
    readings = {
        str(name): array.rename({array.dims[0]: "time"})
        for name, array in dataset.data_vars.items()
        if not str(name).endswith(INPUT_VOLTS_SUFFIX)
    }
    return xr.Dataset(readings)
```

On an hour of the demo stack that turns 1344 readings into a grid of
roughly 8000 cells, of which **83% are NaN**. The sensors run at three
different rates, and even `beam-x` and `beam-y` — same instrument, same
rate — land up to 1 ms apart and so never share a row.

The padding also costs meaning. Once it is there, a NaN no longer
distinguishes a reading that failed from one that was never taken.

So align when channels have to be compared cell by cell, and keep the
ragged shape otherwise. Keeping it an explicit step rather than folding
it into the loader is deliberate: the cost should be visible at the
point it is paid.

## Why netCDF is shaped differently

netCDF stores named arrays over named dimensions. There is no row — the
closest thing is an index shared across several arrays, which means a
long table has to be rebuilt on the way in and out rather than simply
read.

The obvious fix is CF's **discrete sampling geometries**, whose ragged
array layout is a long table in all but name: one `obs` dimension, with
`time(obs)`, `value(obs)` and a per-station `row_size`. That would match
the other three formats exactly, and labmon does not use it, because a
single `value(obs)` variable can carry exactly one `units` attribute.
An export spans K, °C, mbar, V, µm, mW and Hz at once. DSG assumes one
physical quantity per collection, and this is deliberately not that.

One variable per sensor is the layout that keeps the unit attached to
the readings it describes — which is the whole point of recording it.
