# Changelog

All notable changes to labmon are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Commits follow [Conventional Commits](https://www.conventionalcommits.org/),
so the sections below map onto commit types.

## [Unreleased]

Nothing yet.

## [0.3.0] — 2026-09-02

### Added

#### The command line

- **`labmon query` and `labmon export`** — print readings as a table,
  or write them to CSV, Parquet, Feather or netCDF (netCDF is opt-in as
  `labmon[netcdf]`). Both take the same selection flags, and the unit
  travels with the readings in every format.
  [`docs/export.md`](docs/export.md),
  [`docs/loading-exports.md`](docs/loading-exports.md) —
  [#154](https://github.com/quentinmarolleau/labmon/pull/154)
  - `--split-per-sensor` writes one file per sensor; `--no-raw-input`
    leaves the provenance columns out.
- **`labmon query latest`** — one row per sensor, with how long ago it
  reported. `--stats` adds the window's average, deviation and count,
  the average rounded against the deviation.
  [#164](https://github.com/quentinmarolleau/labmon/pull/164),
  [#171](https://github.com/quentinmarolleau/labmon/pull/171)
- **`labmon sensors`** — the roster of every sensor labmon has seen,
  cached between runs, so one that stops reporting is still listed.
  [#165](https://github.com/quentinmarolleau/labmon/pull/165)
- **`labmon monitor`** — a panel that redraws in place, for the
  terminal beside the experiment. A tile per sensor from
  `[[monitor.panels]]`, with thresholds and per-sensor digits, or a
  table of everything when no layout is configured. Needs the `tui`
  extra.
  [`docs/monitor.md`](docs/monitor.md) —
  [#172](https://github.com/quentinmarolleau/labmon/pull/172),
  [#177](https://github.com/quentinmarolleau/labmon/pull/177)
- **Tab completion** for bash, zsh, fish and powershell, through
  `labmon --install-completion`.
  [#154](https://github.com/quentinmarolleau/labmon/pull/154)
- **`labmon init`** — issues the instance's admin token, writes it into
  `.env`, and creates the database. Replaces exec'ing into the container
  and copying the token out of the terminal, and works from a machine
  that has no container to exec into. `--retention` sets how long
  readings are kept, which can only be decided when a database is
  created.
  [`docs/deployment.md`](docs/deployment.md#setting-up-and-starting-over)
- **`labmon reset-database`** — empties the database and creates it
  again, keeping its retention. The admin token is untouched, so clients
  keep writing. `docker compose down -v` does not do this: InfluxDB's
  data is a bind mount rather than a named volume.
  [`docs/deployment.md`](docs/deployment.md#setting-up-and-starting-over)

#### Elsewhere

- **A per-user configuration file**, read from
  `$XDG_CONFIG_HOME/labmon/labmon.toml`: the display timezone, and the
  `[monitor]` section the panel reads.
  [`docs/configuration.md`](docs/configuration.md) —
  [#170](https://github.com/quentinmarolleau/labmon/pull/170)
- Simulated readings are rounded to a plausible instrument resolution,
  through `--resolution` or `--significant-digits`.
  [#153](https://github.com/quentinmarolleau/labmon/pull/153)
- **labmon is on PyPI.** `uv tool install labmon` (or
  `pip install labmon`) puts the command line on a machine with no
  checkout, which is all a sensor host or an analysis laptop ever
  needed. The stack still comes from the repository — a compose file is
  not something an installer can usefully deliver.
  [`docs/client-setup.md`](docs/client-setup.md) —
  [#169](https://github.com/quentinmarolleau/labmon/issues/169)
- **`labmon --version`**, and a `py.typed` marker so an installed
  labmon's annotations reach a downstream type checker.
  [#169](https://github.com/quentinmarolleau/labmon/issues/169)

### Changed

- `mock-sensor` and `serial-sensor` are now `labmon mock-sensor` and
  `labmon serial-sensor`. The old spellings still work and warn.
- A calibrated reading is stored at the resolution of its input rather
  than at full float64 precision.
  [#175](https://github.com/quentinmarolleau/labmon/pull/175)
- Startup imports pyarrow, pint, numpy and the InfluxDB client only
  when the command needs them: `labmon query --help` goes from 0.79 s
  to 0.19 s.
- The demo's beam channels wander rather than tracing a Lissajous
  figure.
  [#176](https://github.com/quentinmarolleau/labmon/pull/176)
- Commands read `.env` from the directory they run in, so a token set
  for Compose also reaches a `labmon` typed at a prompt. The process
  environment still wins.
  [#185](https://github.com/quentinmarolleau/labmon/issues/185)
- `INFLUXDB_NODE_ID` defaults to `node0`, leaving the token as the only
  value the quickstart has to fill in.
- `query latest` asks `information_schema` once for every table rather
  than once per table. A `labmon monitor` tick goes from 83 ms to
  60 ms against the demo stack, and the saving grows with the number of
  measurements a deployment writes.
  [#179](https://github.com/quentinmarolleau/labmon/pull/179)
- Grafana 13.2.0, Loki 3.7.7, Alloy 1.19.2, Caddy 2.11.

### Fixed

- `labmon mock-sensor` given neither `--measurement` nor `--unit` wrote
  to `temperature` with no unit; both are now required.
  [#158](https://github.com/quentinmarolleau/labmon/issues/158)
- Simulated sensors reported full float64 precision, filling the
  database with readings no thermometer could produce.
  [#152](https://github.com/quentinmarolleau/labmon/issues/152)
- A Grafana panel plugin that could not be fetched stopped the whole
  stack starting, crash-looping the container behind an `unhealthy`
  status that named no cause. The install is now asynchronous: the
  failure is logged, Grafana serves, and the panel reads "plugin not
  found".
  [#186](https://github.com/quentinmarolleau/labmon/issues/186)

### Security

- Containers built from the labmon image run as an unprivileged system
  user rather than as root. Serial access comes from `dialout`
  membership; a host that numbers that group differently passes its own
  gid with `group_add`.
  [`docs/serial-sensor.md`](docs/serial-sensor.md) —
  [#116](https://github.com/quentinmarolleau/labmon/issues/116)
- The server's Alloy UI binds loopback, as the client's has since
  [#63](https://github.com/quentinmarolleau/labmon/pull/63).
  It has no authentication and describes the deployment, so reaching it
  from a workstation now needs
  `ssh -L 12345:127.0.0.1:12345 <host>` — the instruction
  [`docs/client-setup.md`](docs/client-setup.md) already gave for the
  client's.
  [#136](https://github.com/quentinmarolleau/labmon/issues/136)

## [0.2.0-beta.1] — 2026-08-23

The first beta. `RELEASE_NOTE.md` carries whichever release is current,
so the beta's announcement is on
[its release page](https://github.com/quentinmarolleau/labmon/releases/tag/v0.2.0-beta.1).

[Unreleased]: https://github.com/quentinmarolleau/labmon/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/quentinmarolleau/labmon/releases/tag/v0.3.0
[0.2.0-beta.1]: https://github.com/quentinmarolleau/labmon/releases/tag/v0.2.0-beta.1
