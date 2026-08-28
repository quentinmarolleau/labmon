# Changelog

All notable changes to labmon are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Commits follow [Conventional Commits](https://www.conventionalcommits.org/),
so the sections below map onto commit types.

## [Unreleased]

### Added

- **A terminal panel for watching current values.** `labmon monitor`
  draws where every sensor stands and redraws itself, for the terminal
  already open beside the experiment — where Grafana is the wrong tool,
  and over a bare SSH session is no tool at all.
  [`docs/monitor.md`](docs/monitor.md) —
  [#172](https://github.com/quentinmarolleau/labmon/pull/172),
  [#177](https://github.com/quentinmarolleau/labmon/pull/177)
  - **A tile per sensor**, from `[[monitor.panels]]`: the reading large
    enough to read across a room, its unit, how long ago it arrived,
    and a border that changes colour when it crosses `warn_above` or
    `warn_below`. With no layout the panel falls back to a table of
    every sensor the database has.
  - **A sensor that has gone quiet still shows what it was reading**,
    marked as not reporting. A cryostat that went quiet at 4 K is a
    different morning from one that went quiet at 300 K.
  - **`[[monitor.sensors]]` display rules** say how many digits an
    instrument is worth, in the table and in any tile alike — for the
    readings behind a calibration, where a conversion turns four honest
    digits into seventeen.
  - `q` quits, `r` changes the refresh rate, `s` writes a screenshot,
    `m` opens the command palette — where a theme is worn as the
    selector passes over it — and `?` lists the keys.
  - Needs the `tui` extra: `pip install 'labmon[tui]'`. An install that
    only writes readings does not carry Textual.
- **`labmon query latest`, one row per sensor and how long ago it
  reported.** Every measurement reduced to its most recent reading in a
  single round trip, with the stale ones coloured.
  [`docs/export.md`](docs/export.md) —
  [#163](https://github.com/quentinmarolleau/labmon/pull/163),
  [#164](https://github.com/quentinmarolleau/labmon/pull/164),
  [#165](https://github.com/quentinmarolleau/labmon/pull/165)
  - **`labmon sensors`** lists the roster: every sensor labmon has seen,
    cached between runs, so one that has stopped reporting is still on
    the list rather than silently absent.
  - **`--stats`** adds the window's average, standard deviation and
    reading count beside each value, rounded against each other so the
    average is never quoted finer than the spread supports.
    [#171](https://github.com/quentinmarolleau/labmon/pull/171)
- **A per-user configuration file**, read from
  `$XDG_CONFIG_HOME/labmon/labmon.toml`: the timezone timestamps are
  shown in, and the `[monitor]` section the panel reads. Not having one
  is the ordinary case — every key has a default.
  [`docs/configuration.md`](docs/configuration.md) —
  [#170](https://github.com/quentinmarolleau/labmon/pull/170)
- **A command line for reading recorded data.** `labmon query` prints
  readings as an aligned table; `labmon export` writes them to a file.
  Both take the same selection flags — `--measurement`, `--sensor-id`,
  `--since`, `--until` — so whichever is learned first teaches the
  other.
  [`docs/export.md`](docs/export.md) —
  [#154](https://github.com/quentinmarolleau/labmon/pull/154)
  - **Four export formats.** CSV, Parquet and Feather need nothing
    extra, since pyarrow already arrives with the InfluxDB client.
    netCDF is opt-in as `labmon[netcdf]`, so a sensor host that only
    writes does not pay for it.
  - **The unit travels with the readings**, as a column in every format
    and additionally as Arrow field metadata in Parquet and Feather,
    and as a CF `units` attribute in netCDF. pandas and polars both
    discard schema metadata on read, which is why the column is not
    optional.
  - **Tab completion** for bash, zsh, fish and powershell, via
    `labmon --install-completion`. Completing a flag shows its help
    text beside it.
  - `--split-per-sensor`, writing one file per sensor rather than one
    monolithic file.
  - `--no-raw-input`, leaving the `input_volts` and `calibration_id`
    provenance columns out of an export.
  - **Documentation for loading an export back**, covering all four
    files in pandas, polars and xarray, and what the shape of the data
    means once it is there.
    [`docs/loading-exports.md`](docs/loading-exports.md)
- Simulated readings are rounded to a plausible instrument resolution,
  through `--resolution` for an absolute step or `--significant-digits`
  otherwise.
  [#153](https://github.com/quentinmarolleau/labmon/pull/153)

### Changed

- **A calibrated reading is stored at the resolution its input had.**
  `serial-sensor` wrote the full float64 result of a conversion, so an
  exported column claimed sixteen digits of a twelve-bit measurement
  and nothing said which of them were physical.
  [#175](https://github.com/quentinmarolleau/labmon/pull/175)
- The demo's beam channels wander as a beam does, rather than tracing
  the tidy Lissajous figure two sines at incommensurate periods gave
  them.
  [#176](https://github.com/quentinmarolleau/labmon/pull/176)
- **`mock-sensor` and `serial-sensor` are now `labmon mock-sensor` and
  `labmon serial-sensor`.** The old spellings still work and print a
  deprecation warning, so unit files already installed on lab machines
  keep running.
- Startup no longer imports pyarrow, pint, numpy and the InfluxDB
  client unless the command being run needs them. `labmon query --help`
  goes from 0.79 s to 0.19 s, and a tab completion from roughly 1.8 s
  to 0.4 s.

### Fixed

- `labmon mock-sensor` with no `--measurement` and no `--unit` wrote a
  walk around 21.0 into the `temperature` table with no unit tag. Both
  are now required.
  [#158](https://github.com/quentinmarolleau/labmon/issues/158)
- Simulated sensors reported values to full float64 precision, filling
  the database with readings like `76.85006139177405 K` that no
  thermometer could produce.
  [#152](https://github.com/quentinmarolleau/labmon/issues/152)

## [0.2.0-beta.1] — 2026-08-23

The first beta. See
[`RELEASE_NOTE.md`](RELEASE_NOTE.md) for the full announcement.

[Unreleased]: https://github.com/quentinmarolleau/labmon/compare/v0.2.0-beta.1...HEAD
[0.2.0-beta.1]: https://github.com/quentinmarolleau/labmon/releases/tag/v0.2.0-beta.1
