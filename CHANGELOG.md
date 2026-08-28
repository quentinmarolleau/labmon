# Changelog

All notable changes to labmon are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Commits follow [Conventional Commits](https://www.conventionalcommits.org/),
so the sections below map onto commit types.

## [Unreleased]

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

#### Elsewhere

- **A per-user configuration file**, read from
  `$XDG_CONFIG_HOME/labmon/labmon.toml`: the display timezone, and the
  `[monitor]` section the panel reads.
  [`docs/configuration.md`](docs/configuration.md) —
  [#170](https://github.com/quentinmarolleau/labmon/pull/170)
- Simulated readings are rounded to a plausible instrument resolution,
  through `--resolution` or `--significant-digits`.
  [#153](https://github.com/quentinmarolleau/labmon/pull/153)

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

### Fixed

- `labmon mock-sensor` given neither `--measurement` nor `--unit` wrote
  to `temperature` with no unit; both are now required.
  [#158](https://github.com/quentinmarolleau/labmon/issues/158)
- Simulated sensors reported full float64 precision, filling the
  database with readings no thermometer could produce.
  [#152](https://github.com/quentinmarolleau/labmon/issues/152)

## [0.2.0-beta.1] — 2026-08-23

The first beta. See
[`RELEASE_NOTE.md`](RELEASE_NOTE.md) for the full announcement.

[Unreleased]: https://github.com/quentinmarolleau/labmon/compare/v0.2.0-beta.1...HEAD
[0.2.0-beta.1]: https://github.com/quentinmarolleau/labmon/releases/tag/v0.2.0-beta.1
