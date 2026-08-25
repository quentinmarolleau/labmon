# Changelog

All notable changes to labmon are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Commits follow [Conventional Commits](https://www.conventionalcommits.org/),
so the sections below map onto commit types.

## [Unreleased]

### Added

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

- **`mock-sensor` and `serial-sensor` are now `labmon mock-sensor` and
  `labmon serial-sensor`.** The old spellings still work and print a
  deprecation warning, so unit files already installed on lab machines
  keep running.
- Startup no longer imports pyarrow, pint, numpy and the InfluxDB
  client unless the command being run needs them. `labmon query --help`
  goes from 0.79 s to 0.19 s, and a tab completion from roughly 1.8 s
  to 0.4 s.

### Fixed

- Simulated sensors reported values to full float64 precision, filling
  the database with readings like `76.85006139177405 K` that no
  thermometer could produce.
  [#152](https://github.com/quentinmarolleau/labmon/issues/152)

## [0.2.0-beta.1] — 2026-08-23

The first beta. See
[`RELEASE_NOTE.md`](RELEASE_NOTE.md) for the full announcement.

[Unreleased]: https://github.com/quentinmarolleau/labmon/compare/v0.2.0-beta.1...HEAD
[0.2.0-beta.1]: https://github.com/quentinmarolleau/labmon/releases/tag/v0.2.0-beta.1
