<!-- Not wrapped, on purpose. This file is the body of the GitHub release,
     read by `gh release create --notes-file`, and GitHub reflows prose in
     the browser. Hard-wrapping it puts ragged line breaks on the release
     page. One paragraph or list item per line, however long. -->

# labmon v0.3.0

labmon records any quantity of interest — e.g. cryostat temperature, chamber pressure, laser power, a magnet's current etc. — into a time-series database, and puts it on a live dashboard anyone in the room can open in a browser. It is built from [InfluxDB 3](https://docs.influxdata.com/influxdb3/core/) and [Grafana](https://grafana.com/docs/grafana/latest/), wired together and pre-configured so neither has to be set up by hand.

The beta could record and display. This release adds the third thing a lab actually does with its measurements: read them back at a prompt. It also stops requiring a clone to do it.

## Trying it

Unchanged from the beta — two commands and a browser tab, no hardware:

```bash
cp .env.example .env
docker compose up -d --wait
```

If you already have an InfluxDB 3 instance and want only the command line against it, that is now one line and no repository:

```bash
uv tool install 'labmon[tui]'
```

## Installing it

**labmon is on PyPI.** `uv tool install labmon`, or `pip install labmon`. The commands are a client — they speak HTTP to the server, read nothing from the repository, and work on a machine with neither a container nor a copy of the source. A sensor host used to need a full development checkout to run one command; it now needs the package and three environment variables. [`docs/client-setup.md`](docs/client-setup.md) — [#169](https://github.com/quentinmarolleau/labmon/issues/169)

The stack still comes from the repository. A compose file, Grafana's provisioning and the demo feeder are not things an installer can usefully deliver, so `git clone` remains the answer for anyone standing the server up.

Extras split the Raspberry Pi from the analysis laptop: `labmon[tui]` for the terminal panel, `labmon[netcdf]` for netCDF export, `labmon[spline]` for spline calibration. Serial support is in the base install.

## Reading the data

- **`labmon query`** prints readings as a table, and **`labmon export`** writes the same selection to CSV, Parquet, Feather or netCDF. They share one set of selection flags — `--measurement`, `--sensor-id`, `--since`, `--until` — so learning one teaches the other, and the unit travels with the readings in every format. [`docs/export.md`](docs/export.md) · [`docs/loading-exports.md`](docs/loading-exports.md) — [#154](https://github.com/quentinmarolleau/labmon/pull/154)
- **`labmon query latest`** answers a different question: what every sensor reads right now, and how long ago each last spoke, so a silent one shows up with its last reading rather than disappearing. `--stats` adds the window's average, deviation and count, the average rounded against the deviation. [#164](https://github.com/quentinmarolleau/labmon/pull/164), [#171](https://github.com/quentinmarolleau/labmon/pull/171)
- **`labmon sensors`** lists every sensor labmon has seen, cached between runs, so one that has stopped reporting is still listed. [#165](https://github.com/quentinmarolleau/labmon/pull/165)
- **`labmon monitor`** redraws in place, for the terminal already open beside the experiment and for a bare SSH session where a browser is not an option. Name the handful of things you care about and it becomes a grid of tiles — the value large enough to read across the room, with a colour change when a reading leaves the range you set. Name nothing and it draws a table of everything. [`docs/monitor.md`](docs/monitor.md) — [#172](https://github.com/quentinmarolleau/labmon/pull/172), [#177](https://github.com/quentinmarolleau/labmon/pull/177)
- **Tab completion** for bash, zsh, fish and powershell, through `labmon --install-completion`. Generated from the command signatures, so a new flag is completable with no second step. [#154](https://github.com/quentinmarolleau/labmon/pull/154)

## Setting the database up

- **`labmon init`** asks the server for its admin token, writes it into `.env`, and creates the database. It replaces exec'ing into the container and copying the token out of the terminal, and works from a machine that has no container to exec into. `--retention` sets how long readings are kept, which can only be decided when a database is created. [`docs/deployment.md`](docs/deployment.md#setting-up-and-starting-over)
- **`labmon reset-database`** empties the database and creates it again, keeping its retention. The admin token is untouched, so clients keep writing. `docker compose down -v` does not do this: InfluxDB's data is a bind mount rather than a named volume. [`docs/deployment.md`](docs/deployment.md#setting-up-and-starting-over)

There is no delete-by-time and no delete-by-sensor, and there will not be under the current schema. InfluxDB 3 Core's query API is read-only, and what it can drop is a whole database or a whole table — nothing finer.

## Recording

- **A per-user configuration file** at `$XDG_CONFIG_HOME/labmon/labmon.toml` carries the display timezone and the `[monitor]` section the panel reads, so a preference is written once rather than passed on every invocation. [`docs/configuration.md`](docs/configuration.md) — [#170](https://github.com/quentinmarolleau/labmon/pull/170)
- **Simulated readings are rounded to a plausible instrument resolution**, through `--resolution` or `--significant-digits`. A mock sensor was filling the database with float64 digits no thermometer could produce. [#153](https://github.com/quentinmarolleau/labmon/pull/153)
- **A calibrated reading is stored at the resolution of its input** rather than at full float64 precision. A 60 µm/V conversion turns four honest digits into seventeen, and only the first four mean anything. [#175](https://github.com/quentinmarolleau/labmon/pull/175)
- **Commands read `.env` from the directory they run in**, so a token set for Compose also reaches a `labmon` typed at a prompt. The process environment still wins. [#185](https://github.com/quentinmarolleau/labmon/issues/185)
- **Startup no longer imports what the command will not use.** pyarrow, pint, numpy and the InfluxDB client are pulled in by the commands that need them: `labmon query --help` goes from 0.79 s to 0.19 s.

## Security

- **Containers run as an unprivileged user.** The image had no `USER`, so every container built from it ran as uid 0 — six mock sensors, the demo feeder, `serial-sensor`. None of them needs it. Serial access now comes from `dialout` membership; a host that numbers that group differently passes its own gid with `group_add`. [`docs/serial-sensor.md`](docs/serial-sensor.md) — [#116](https://github.com/quentinmarolleau/labmon/issues/116)
- **The server's Alloy UI binds loopback**, as the client's has since the beta. It has no authentication and describes the deployment, so reaching it from a workstation now needs `ssh -L 12345:127.0.0.1:12345 <host>` — the same habit [`docs/client-setup.md`](docs/client-setup.md) already prescribed for the client's. [#136](https://github.com/quentinmarolleau/labmon/issues/136)

The rest of the posture is unchanged from the beta: plaintext ports bind to loopback by default, the LAN-facing path is the `tls` profile, and Loki is reachable only through the proxy, behind a credential, for writing only. [`SECURITY.md`](SECURITY.md) carries the private disclosure route.

## Fixed

- **A Grafana panel plugin that could not be fetched stopped the whole stack starting**, crash-looping the container behind an `unhealthy` status that named no cause — on an offline bench, behind an intercepting proxy, or whenever grafana.com was unreachable. The install is now asynchronous: the failure is logged, Grafana serves, and the panel reads "plugin not found". [#186](https://github.com/quentinmarolleau/labmon/issues/186)
- **`labmon mock-sensor` given neither `--measurement` nor `--unit`** wrote to `temperature` with no unit. Both are now required. [#158](https://github.com/quentinmarolleau/labmon/issues/158)

## Upgrading from v0.2.0-beta.1

`mock-sensor` and `serial-sensor` are now `labmon mock-sensor` and `labmon serial-sensor`. The old spellings still work and warn once at startup, because they shipped in the beta and are therefore in compose files and systemd units labmon does not control. They go in 1.0.

Nothing else is a breaking change. Recorded data, the schema and the dashboards are untouched.

## Known limits

- **The serial path has never met a physical board.** It is tested end to end against a virtual serial port and against a feeder speaking the firmware's wire format, but the last centimetre is unproven. Check readings against a known voltage before trusting them. [`docs/serial-sensor.md`](docs/serial-sensor.md) · [`docs/demo-stack.md`](docs/demo-stack.md)
- **Traffic is plaintext unless the `tls` profile is on.** Deliberate — it assumes a trusted lab network — and it is a setting, not a rewrite. [`docs/deployment.md`](docs/deployment.md#encrypting-client-and-viewer-traffic)
- **A long outage is recorded as its final stretch.** The queue holds a fixed number of points, so what survives is the most recent window rather than the whole gap. Keeping the entire window at reduced resolution is tracked in [#141](https://github.com/quentinmarolleau/labmon/issues/141).
- **A sensor reports one value per reading.** An instrument that measures several quantities at once needs one sensor process per quantity, which is tracked in [#168](https://github.com/quentinmarolleau/labmon/issues/168).
- **There is no hosted documentation site.** Everything is markdown in `docs/`, read on GitHub — [#55](https://github.com/quentinmarolleau/labmon/issues/55).

## What 1.0 means

Verified against real hardware, and running in at least two different labs for at least six months.

Deliberately slow. Monitoring software is trusted by default once installed — nobody re-derives whether the number on the dashboard is right — and the failure mode of getting it wrong is a plausible-looking number that is quietly incorrect. The version number should stay honest about how much has been proven outside one bench.

## Quality bar

100% test coverage, now measured over `demo/`, `scripts/` and `templates/` as well as the package — three trees a user meets early and none of which was previously measured. basedpyright on its strictest preset with zero errors and zero warnings, and nine more ruff rule families than the beta shipped with.

The cold-start job still builds the whole stack from nothing on every push and asserts that data arrives, that every dashboard's queries execute, and that the TLS and log paths behave — including the negative cases, so a proxy serving no TLS at all would fail rather than pass. [#85](https://github.com/quentinmarolleau/labmon/issues/85), [#88](https://github.com/quentinmarolleau/labmon/issues/88), [#118](https://github.com/quentinmarolleau/labmon/issues/118), [#135](https://github.com/quentinmarolleau/labmon/issues/135)

[`CONTRIBUTING.md`](CONTRIBUTING.md) has the reasoning and the workflow.
