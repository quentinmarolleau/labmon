# Getting the data out

Two commands read recorded readings, and they differ only in what they
do with the answer:

```bash
labmon query  --measurement temperature --since 5m       # look at it now
labmon export --measurement temperature --since 5m -o run # keep it
```

Both take the same four selection flags — `--measurement`,
`--sensor-id`, `--since`, `--until` — so whichever you learn first
teaches the other. `query` prints an aligned table for a person;
`export` writes a file for a machine.

```bash
labmon export --sensor-id cryo-77k --since 2026-08-01 --until 2026-08-02
labmon export --measurement temperature --since 24h --format parquet
labmon export --since 5m --format feather -o test
labmon export --since 7d --split-per-sensor -o run.feather
labmon export --since 1h -o - | head
```

It reads the same `INFLUXDB_HOST`, `INFLUXDB_DATABASE`,
`INFLUXDB3_AUTH_TOKEN` and `INFLUXDB_TLS_CA` settings as everything else
— see [`configuration.md`](configuration.md) — so it works from a client
machine as well as from the server.

## Formats

| Format | Flag | Reach for it when |
|---|---|---|
| CSV | `--format csv` (default) | Handing a file to somebody, or piping into another tool |
| Parquet | `--format parquet` | Anything large: columnar, compressed, typed |
| Feather | `--format feather` | Fast round-trips between scripts on one machine |
| netCDF | `--format netcdf` | Working in xarray, or sharing with people who expect CF conventions |

With no `--format`, the extension of `-o` decides; failing that, CSV.

The extension is appended for you when `-o` does not already carry it,
so `-o test --format feather` writes `test.feather` and a directory does
not fill with extensionless files nothing can identify. Only the right
extension counts as already present — `-o run.2026-08-24` becomes
`run.2026-08-24.csv`, because `.24` names no format. A path ending in a
*different* format's extension is left exactly as typed and warns:
`-o run.csv --format parquet` contradicts itself, and quietly renaming
the filename you were explicit about would be the surprising half of
that.

### Where the file lands

`-o` takes a path in whatever shape you type it:

```bash
labmon export --since 2m -o exported/data            # exported/data.csv
labmon export --since 2m -o ~/data/labmon/2026-08-25 # tilde expanded
labmon export --since 2m -o exported/                # exported/labmon-export.csv
```

A directory that does not exist yet is created, however deep, so
`-o runs/2026-08-25/data` does not fail because a dated directory is
new. Creating one is logged at INFO, so a typo in the path shows up as a
directory nobody meant to make rather than as silence.

A `~` is expanded here rather than left to the shell, which does not
expand it inside quotes — and a directory literally named `~` is never
what was meant.

A path that names a directory, either because one is already there or
because it ends in a separator, receives the default filename inside it.
Writing `exported.csv` *beside* a directory called `exported` is nobody's
intent.

A path whose parent is an existing *file* is refused, and the file is
left alone.

CSV, Parquet and Feather need no extra install: `pyarrow` already
arrives with the InfluxDB client. netCDF does:

```bash
pip install 'labmon[netcdf]'
```

Loading looks like this:

```python
import pandas as pd, polars as pl, xarray as xr

pd.read_parquet("run.parquet")
pl.read_ipc("run.feather")
xr.open_dataset("run.nc")
```

## What a row looks like

One row per reading, which is what the database holds:

| Column | |
|---|---|
| `time` | UTC, millisecond resolution |
| `sensor_id` | Which sensor |
| `measurement` | Which measurement (the InfluxDB table) |
| `value` | The calibrated reading |
| `unit` | What `value` is in |
| `input_volts` | The voltage it was converted from, when the calibration stores it |
| `calibration_id` | Which calibration produced it |

This is long format, not a `time × sensor` grid. Sensors run at
different rates — the demo alone has 0.2 Hz, 0.5 Hz and 1 Hz at once —
so there is no single time axis to put them on, and a wide table would
have to invent a resampling rule. If you want one for a spreadsheet, it
is one call away:

```python
frame.pivot(index="time", columns="sensor_id", values="value")
```

netCDF is the exception: it is variable-per-sensor by nature, each with
its own time dimension, so no alignment is imposed there either. The two
provenance columns become a companion variable and an attribute:

| long format | netCDF |
|---|---|
| `measurement` | a `measurement` attribute on the reading's variable |
| `unit` | a CF `units` attribute |
| `input_volts` | a `<sensor>_input_volts` variable in volts, on the same time axis |
| `calibration_id` | a `calibration_id` attribute on the reading's variable |

The reading also carries CF's `ancillary_variables`, pointing at its
voltage, so a reader finds it without guessing the naming scheme. A
sensor that never had a voltage — anything simulated — gets no companion
variable at all, because an all-NaN one would suggest a measurement that
was attempted and failed. A sensor recalibrated mid-window lists every
calibration it used, joined, rather than only the last.

## Leaving the provenance out

`--no-raw-input` drops `input_volts` and `calibration_id`:

```bash
labmon export --since 24h --no-raw-input -o readings.csv
# time, sensor_id, measurement, value, unit
```

They are the bulk of a narrow export — about a quarter of the CSV — and
mean nothing to somebody who only wants the readings.

It is a deliberate loss, not a tidy-up. `input_volts` is stored so a
reading can be recomputed when a calibration turns out to have been
wrong; an export without it cannot be corrected after the fact, only
re-run against the database. Keep it for anything archival.

The unit is never dropped. A column of bare numbers nobody can interpret
is the failure the whole calibration layer exists to prevent.

## Units travel with the readings

The `unit` column is on every row, in every format. That is deliberate,
and it is not free in CSV — about 21% on a million rows — so it is worth
saying why.

Parquet and Feather also carry the unit in the Arrow schema, as
field-level metadata on `value` (when the whole file is one unit) and as
a `labmon` manifest recording the export window, the labmon version and
the unit of every sensor. That is the tidier home for it. **But pandas
and polars both discard Arrow metadata on read** — `df.attrs` comes back
empty — so a metadata-only unit would be present in the file and
invisible to every reader this command exists for.

Hence both. The column is what a reader sees; the metadata is what
survives losslessly for anything using `pyarrow` directly.

The column costs nothing in the binary formats, because the label
columns are dictionary-encoded. Measured over a million rows, adding
`unit` costs **+0.06% in Parquet** and **+0.35% in Feather**; left as a
plain string it would cost 44% in Feather, which does not compress by
default. It also arrives as a `category` in pandas and a `Categorical`
in polars rather than as a repeated string.

In netCDF the unit is a CF `units` attribute on each variable, which is
where xarray users already look for it.

## Selecting what to export

- `--measurement` and `--sensor-id` are both repeatable, and default to
  everything. A name that no table matches is refused, with the
  available names listed.
- `--since` and `--until` take either an ISO 8601 timestamp
  (`2026-08-01`, `2026-08-01T14:30:00+02:00`) or a duration meaning that
  long ago (`24h`, `90m`, `7d`). A timestamp with no offset is read as
  UTC, not as local time, so the same command means the same thing on
  every machine in the lab.
- The window defaults to the last hour, and both bounds resolve against
  one reading of the clock, so `--since 24h --until 1h` is a fixed
  23-hour span.
- The window is half-open, `[since, until)`, so two back-to-back exports
  neither duplicate nor drop the reading that lands on the boundary.

## One file or many

By default everything lands in one file. `--split-per-sensor` writes
`<name>_<sensor_id>.<ext>` instead:

```bash
labmon export --since 7d --split-per-sensor -o run.csv
# run_cryo-77k.csv  run_room-1.csv  run_vac-1.csv
```

Each split file holds one sensor, so each one carries its unit as
field-level metadata too — which a mixed file cannot, since a
field-level unit would be read as covering every row.

A sensor id has to be usable in a filename to be split on: at most 64
characters of letters, digits, dot, dash or underscore. Anything else is
refused rather than mangled, because a sensor id comes from an
operator-edited calibration file and a `/` in one would write somewhere
nobody asked for.

## Piping

`-o -` writes to stdout:

```bash
labmon export --since 1h -o - --log-level WARNING > run.csv
```

Logs go to stderr, so the stream stays clean. netCDF cannot be piped —
its writer seeks — and `--split-per-sensor` cannot either, since it
produces several files; both say so rather than producing something
broken.

## Notes

- A window that matches nothing still writes a valid empty file, with
  the full set of columns, and warns. A script checking for the file
  finds one.
- Rows come back in time order, and measurements are read in a stable
  order, so two exports over unchanged data produce identical files.
- Timestamps are truncated to milliseconds, which is the resolution
  sensors record at. A sub-millisecond stamp written by some other tool
  is truncated rather than refused.

## Looking at readings without writing a file

```bash
labmon query --measurement temperature --since 5m
labmon query --sensor-id cryo-77k --since 24h --limit 50
labmon query --since 1h --limit 0
```

```
time                     sensor_id   measurement  value    unit
-----------------------  ----------  -----------  -------  ----
2026-08-24 15:33:24.186  room-1      temperature  20.952   °C
2026-08-24 15:33:24.188  cryo-4k     temperature  4.206    K
2026-08-24 15:33:24.216  cryo-77k    temperature  77.132   K

showing the last 3 of 539 readings
```

The most recent readings come last, the way a log reads. `--limit`
defaults to 20 and `--limit 0` shows everything; when rows are dropped
the footer says so, because a silently shortened table is one somebody
draws a conclusion from.

Only the columns that carry meaning are shown, and a column that is
empty for every row in the result is left out entirely. `calibration_id`
and `input_volts` are deliberately absent — they are provenance, they
are in every exported file, and a hash column pushes the value off the
screen.

`query` never emits binary. To pipe a real format into another tool, use
`labmon export -o -`.

## Tab completion

```bash
labmon --install-completion          # detects your shell
labmon --install-completion fish     # or name it
labmon --show-completion fish        # print it instead, to install by hand
```

bash, zsh, fish and powershell are supported. Completing an option shows
its help text next to it, so the flags are documented where they are
typed:

```
$ labmon export --format <TAB>
csv      Output format: csv (.csv), parquet (.parquet)…
parquet  Output format: csv (.csv), parquet (.parquet)…
feather  Output format: csv (.csv), parquet (.parquet)…
netcdf   Output format: csv (.csv), parquet (.parquet)…
```

The script is generated from the command signatures themselves, so there
is nothing separate to keep in step with them — adding a flag makes it
completable with no second step.

**`labmon` has to be on your PATH for any of this to fire.** A shell
attaches completions to a command word, and `uv run labmon …` is the
command `uv`. Installing it as a tool puts it on the PATH:

```bash
uv tool install --editable .
```
