# labmon

Records experiment readings — cryostat temperature, chamber pressure,
laser power, a magnet's current — into [InfluxDB 3][influx], and reads
them back three ways: a command line, a terminal panel, and Grafana in a
browser.

This package is the Python half: the sensors, the calibration, the writer
and the `labmon` command. The other half is a Docker Compose stack —
InfluxDB, Grafana and its provisioned dashboards, Loki, Alloy — which
lives in the [repository][repo] and cannot usefully be delivered by an
installer.

## Install

```bash
uv tool install labmon          # or: pip install labmon
```

Nothing else is needed if you already have an InfluxDB 3 instance to talk
to. Three environment variables point at it, in the process environment or
in a `.env` beside you:

| | |
|---|---|
| `INFLUXDB_HOST` | where the server is, e.g. `http://192.168.1.50:8181` |
| `INFLUXDB_DATABASE` | which database to use, e.g. `lab` |
| `INFLUXDB3_AUTH_TOKEN` | the admin token InfluxDB issued |

If you have no instance yet, `labmon init` mints the token and creates the
database over HTTP, from any machine.

## What you get

| | |
|---|---|
| `labmon query` | print readings as a table; `query latest` gives one row per sensor, with how long ago each spoke |
| `labmon export` | the same selection, written to CSV, Parquet, Feather or netCDF, with the unit carried along |
| `labmon monitor` | a panel that redraws in place, as a grid of tiles or a table of everything |
| `labmon sensors` | every sensor labmon has seen, cached, so one that stopped reporting is still listed |
| `labmon serial-sensor` | read a board over a serial port, converting raw counts through a calibration file |
| `labmon mock-sensor` | invent a plausible reading, for confirming an address and a token before wiring anything up |
| `labmon init`, `labmon reset-database` | set the database up, or wipe it and start again |

## Extras

| | |
|---|---|
| `labmon[tui]` | Textual, which `labmon monitor` needs |
| `labmon[netcdf]` | netCDF export. CSV, Parquet and Feather need nothing extra |
| `labmon[spline]` | SciPy, for the `spline` calibration mode |

Combine them as `labmon[tui,netcdf]`.

## Documentation

Everything is in the [repository][repo]: the
[README][readme] for the whole stack, and
[`docs/`][docs] for one page per component —
[client setup][client], [export][export], [the monitor panel][monitor],
[serial sensors][serial], [Grafana][grafana], [deployment][deployment].

Licensed GPLv3.

[influx]: https://docs.influxdata.com/influxdb3/core/
[repo]: https://github.com/quentinmarolleau/labmon
[readme]: https://github.com/quentinmarolleau/labmon/blob/main/README.md
[docs]: https://github.com/quentinmarolleau/labmon/tree/main/docs
[client]: https://github.com/quentinmarolleau/labmon/blob/main/docs/client-setup.md
[export]: https://github.com/quentinmarolleau/labmon/blob/main/docs/export.md
[monitor]: https://github.com/quentinmarolleau/labmon/blob/main/docs/monitor.md
[serial]: https://github.com/quentinmarolleau/labmon/blob/main/docs/serial-sensor.md
[grafana]: https://github.com/quentinmarolleau/labmon/blob/main/docs/grafana.md
[deployment]: https://github.com/quentinmarolleau/labmon/blob/main/docs/deployment.md
