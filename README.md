<h1 align="center">
  <img alt="labmon" src="docs/assets/images/header.png" width="420">
</h1>

<p align="center">
  <a href="https://github.com/quentinmarolleau/labmon/actions/workflows/ci.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/quentinmarolleau/labmon/ci.yml?branch=main&amp;label=tests&amp;logo=githubactions&amp;logoColor=white"></a>
  <a href="https://codecov.io/gh/quentinmarolleau/labmon"><img alt="Coverage" src="https://img.shields.io/codecov/c/github/quentinmarolleau/labmon?logo=codecov&amp;logoColor=white"></a>
  <a href="https://github.com/DetachHead/basedpyright"><img alt="Checked with basedpyright" src="https://img.shields.io/badge/types-basedpyright-FFD43B?logo=python&amp;logoColor=white"></a>
  <a href="https://github.com/astral-sh/ruff"><img alt="Linted with Ruff" src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json"></a>
  <a href="LICENSE"><img alt="License GPLv3" src="https://img.shields.io/badge/license-GPLv3-A42E2B?logo=gnu&amp;logoColor=white"></a>
  <br>
  <a href="pyproject.toml"><img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&amp;logoColor=white"></a>
  <a href="https://docs.astral.sh/uv/"><img alt="uv" src="https://img.shields.io/badge/uv-DE5FE9?logo=uv&amp;logoColor=white"></a>
  <a href="https://docs.docker.com/compose/"><img alt="Docker Compose" src="https://img.shields.io/badge/Compose-2496ED?logo=docker&amp;logoColor=white"></a>
  <a href="https://docs.influxdata.com/influxdb3/core/"><img alt="InfluxDB 3" src="https://img.shields.io/badge/InfluxDB_3-22ADF6?logo=influxdb&amp;logoColor=white"></a>
  <a href="https://grafana.com/docs/grafana/latest/"><img alt="Grafana" src="https://img.shields.io/badge/Grafana-F46800?logo=grafana&amp;logoColor=white"></a>
  <a href="https://github.com/quentinmarolleau/labmon/milestones"><img alt="Maturity: beta" src="https://img.shields.io/badge/maturity-beta-yellow"></a>
</p>

labmon records whatever your experiment produces — cryostat temperature,
chamber pressure, laser power, a magnet's current — into a time-series
database, and gives you three ways to look at it: a command line, a live
terminal panel, and a browser dashboard.

```
  WHAT PRODUCES DATA               LABMON                   HOW YOU READ IT

  analog sensor                                          ┌─►  labmon query
        │ volts                                          │      a table, right now
        ▼                                                │
   ADC board ──USB──►  serial-sensor ──┐                 ├─►  labmon export
                                       │                 │      CSV, Parquet,
  instrument with                      │                 │      Feather, netCDF
  a vendor SDK ─────►  your script ────┼──► InfluxDB 3 ──┤
                                       │    time series  ├─►  labmon monitor
  no hardware yet ──►  mock-sensor ────┘                 │      live terminal panel
                                                         │
                                                         └─►  Grafana
                                                                browser dashboard
```

Storage is [InfluxDB 3](https://docs.influxdata.com/influxdb3/core/),
dashboards are [Grafana](https://grafana.com/docs/grafana/latest/); both
come pre-configured, so neither needs setting up by hand.

## Contents

- [Quickstart](#quickstart) — running in a few minutes, no hardware needed
- [Reading the data](#reading-the-data) — `query`, `export`, `monitor`
- [Grafana, for a lab display](#grafana-for-a-lab-display)
- [Connecting a real instrument](#connecting-a-real-instrument)
- [Logs, next to the measurements](#logs-next-to-the-measurements)
- [Running it across the lab](#running-it-across-the-lab)
- [How it works](#how-it-works)
- [Reference](#reference) — configuration, structure, development, status

## Quickstart

You need [Docker](https://docs.docker.com/get-started/get-docker/) and
[uv](https://docs.astral.sh/uv/). Nothing else — no sensors, no hardware.

This sets up the whole stack — database, dashboards, log collection — so it
starts from the repository. If you already have an InfluxDB 3 instance and
want only the command line against it, that is `uv tool install
'labmon[tui]'` and three environment variables; see
[Reading the data](#reading-the-data).

![The whole Quickstart in one terminal session: cloning the repository, copying the settings file, installing the released command line from PyPI with uv, starting InfluxDB, issuing the admin token and creating the database with labmon init, bringing the rest of the stack up, and querying the readings the demo sensors have already written](docs/assets/images/quickstart.gif)

The recording installs the released `labmon` from PyPI. Step 2 below uses
`--editable` instead, which is the better default when you already have the
checkout in front of you; either works.

The four steps below, one at a time.

**1. Get the repository and its settings file.**

```bash
git clone https://github.com/quentinmarolleau/labmon.git
cd labmon
cp .env.example .env
```

**2. Install the command line.**

```bash
uv tool install --editable '.[tui]'
```

`--editable` installs it from the checkout you just made, so `labmon`
follows the working tree. Elsewhere it comes from PyPI, as
`uv tool install 'labmon[tui]'`.

**3. Start InfluxDB and set it up.**

InfluxDB issues its own tokens, so it has to be running before it can give
you one:

```bash
docker compose up -d --wait influxdb
labmon init --retention 1y
```

`labmon init` asks the server for its admin token, writes it into `.env`,
and creates the database. `--retention` is optional; without it, readings
are kept for ever.

> [!IMPORTANT]
> The token is shown once, and one admin token exists per instance.
> `labmon init` saves it, so it is in `.env` — keep that file. If it is
> lost, `influxdb3 create token --admin --regenerate` issues a new one and
> invalidates the old, which means every client needs the new value.

<details>
<summary><b>What <code>labmon init</code> actually does</b></summary>

<br>

Three HTTP calls to InfluxDB, and one edit to a file:

1. `POST /api/v3/configure/token/admin` — unauthenticated, because this is
   the call that issues the credential everything else needs. The server
   answers with the token once and keeps no copy it will hand back.
2. The token is written into `.env` as `INFLUXDB3_AUTH_TOKEN=…`. An
   existing assignment is replaced where it stands, so the comment above
   it still describes the line beneath, and nothing else in the file is
   touched — Compose reads this same file.
3. `POST /api/v3/configure/database` — creates the database named by
   `INFLUXDB_DATABASE`, with the retention you asked for.

Running it again is safe, and does almost nothing: the server answers
`409` to a second token and to a second database of the same name, and
`init` reports both rather than failing. The one thing a re-run *will*
change is the retention, if you pass `--retention` — that is a property of
the database rather than of its creation, so it stays adjustable.

Nothing here needs a container. The token endpoint is served over HTTP
like everything else, so `labmon init` also works from a sensor machine
across the lab, pointed at the server with `INFLUXDB_HOST`.

</details>

<details>
<summary><b>Doing it without installing labmon</b></summary>

<br>

The same thing, from inside the container:

```bash
docker compose exec influxdb influxdb3 create token --admin
```

Then copy the token into `.env` as `INFLUXDB3_AUTH_TOKEN=…` yourself. The
database is created by the first write, so there is nothing else to do —
but it will keep readings for ever, since a retention period can only be
set when the database is created. `labmon init --retention` exists for
exactly that.

</details>

**4. Start everything.**

```bash
docker compose up -d --wait
```

That is the whole setup. Data is already being recorded, so:

```bash
labmon query --since 5m          # readings, as a table
labmon monitor                   # live panel, refreshing in place
```

and [http://localhost:3000](http://localhost:3000) is Grafana, `admin` /
`admin`.

<details>
<summary><b>What is already writing data?</b></summary>

<br>

The `demo` profile is on by default, and it starts two different things:

- **Six simulated sensors** — two room thermometers, two cryogenic
  sensors, a vacuum gauge and a wavemeter — inventing readings already in
  physical units. [`docs/mock-sensor.md`](docs/mock-sensor.md) covers
  adding more or changing what they simulate.
- **Six calibrated channels** running the *real* acquisition path: raw
  ADC counts stream over TCP in the firmware's wire format, and
  `serial-sensor` converts them exactly as it would for a board on a
  serial port. Nothing is mocked but the board itself —
  [`docs/demo-stack.md`](docs/demo-stack.md).

Turning them off for a real deployment is one setting:
`COMPOSE_PROFILES=` in `.env`. See
[`docs/deployment.md`](docs/deployment.md#choosing-what-runs-compose_profiles).

</details>

<details>
<summary><b>The commands say <code>INFLUXDB3_AUTH_TOKEN is not set</code></b></summary>

<br>

`.env` is read by Docker Compose and by `labmon`, but not by your shell.
Run `labmon` from the directory holding `.env` and it picks the token up
from there. From anywhere else, put the setting in your environment:

```bash
set -a; . ./.env; set +a          # bash/zsh
```

or use [direnv](https://direnv.net/) — an `.envrc` is committed for it.
[`docs/configuration.md`](docs/configuration.md) lists every setting and
where it is read.

</details>

## Reading the data

Three commands, one job each. `query` and `export` ask the database the
same question and share the same four selection flags —
`--measurement`, `--sensor-id`, `--since`, `--until` — so learning one
teaches the other.

They are a client and nothing more: they speak HTTP to the server, so the
machine they run on needs no container, no repository and no InfluxDB of
its own.

```bash
uv tool install 'labmon[tui]'    # or: pip install 'labmon[tui]'
```

Then `INFLUXDB_HOST`, `INFLUXDB_DATABASE` and `INFLUXDB3_AUTH_TOKEN`,
in the environment or in a `.env` beside you —
[`docs/configuration.md`](docs/configuration.md). The `tui` extra is
Textual, which only `labmon monitor` needs.

### `labmon query` — a table, now

```bash
labmon query --measurement temperature --since 5m
labmon query --sensor-id cryo-77k --since 24h --limit 50
labmon query latest
```

![Completing labmon query -- at the prompt, which lists every flag with its help text alongside; then a filtered query printing a table of temperature readings; then labmon query latest --stats, one row per sensor with its average, standard deviation and reading count, the two silent sensors in red](docs/assets/images/query.gif)

The flag list at the start is tab completion, one command to install —
see [Tab completion](#tab-completion) below.

`latest` answers a different question: what every sensor reads right now,
and how long ago each last spoke, so a silent one shows up with its last
reading rather than disappearing.

### `labmon export` — a file a notebook can open

```bash
labmon export --measurement temperature --since 24h -o run       # run.csv
labmon export --since 5m --format feather -o test                # test.feather
labmon export --sensor-id cryo-77k --since 2026-08-01 --until 2026-08-02
```

CSV by default; Parquet and Feather for anything large; netCDF for xarray.
All four load in one call:

```python
pd.read_parquet("run.parquet")  # pandas
pl.read_ipc("run.feather")  # polars
xr.open_dataset("run.nc")  # xarray
```

Output is one row per reading — `time`, `sensor_id`, `measurement`,
`value`, `unit`. The unit travels on every row, in every format, so a
column of numbers is never ambiguous between kelvin and celsius.

<details>
<summary><b>Why one row per reading, and not a time × sensor grid?</b></summary>

<br>

Sensors run at different rates, so there is no shared time axis to grid
them onto without resampling — and resampling is a choice about your data
that labmon should not be making silently. `--split-per-sensor` writes one
file per sensor instead of one combined file, which is usually what you
want if you are about to build a grid yourself.

[`docs/loading-exports.md`](docs/loading-exports.md) takes each format
into pandas, polars and xarray, and covers reshaping once it is there.

</details>

### `labmon monitor` — a live panel in the terminal

Useful for the terminal already open next to the experiment, and over a
bare SSH session where a browser is not an option.

![The fallback table, refreshing in place: every sensor the database has, grouped by measurement, with value, unit, age, average, standard deviation and reading count; two sensors that stopped reporting are marked in red](docs/assets/images/monitor-table.gif)

It redraws in place, colours a sensor amber then red as it goes quiet, and
keeps the last good table on screen when the database is briefly
unreachable.

Name the handful of things you actually care about — in a layout passed
with `--config`, or under `[monitor]` in the configuration file when it
should apply to every run — and it becomes a grid of tiles instead: the
value large enough to read across the room, the unit, the age, and a
colour change when a reading leaves the range you set:

![The same panel as a grid of nine tiles: a laser diode tile framed in red because its temperature is over the threshold, and a tile for a sensor that stopped reporting a day ago, dimmed and marked, still showing its last reading](docs/assets/images/monitor-tiles.gif)

### Tab completion

```bash
labmon --install-completion
```

Worth the one command: completing a flag shows its help text beside it, so
the shell doubles as the reference.

> [!TIP]
> [`docs/export.md`](docs/export.md) — formats, time-window spellings,
> completion for bash and zsh ·
> [`docs/monitor.md`](docs/monitor.md) — every key of the tile config ·
> [`monitor.example.toml`](monitor.example.toml) — a worked example

## Grafana, for a lab display

The commands above are for working with the data. Grafana is the other
frontend: a browser dashboard, good for a screen on the wall that the
whole room can see, and for alerting.

![The Lab Overview dashboard: a row of current-value tiles for cold finger, chamber pressure, laser power and bias rail; a panel plotting a calibrated temperature against the raw ADC voltage it came from; cryogenic and room temperature, vacuum and laser power time series; a needle dial for laser detuning; an XY plot of beam position; and a table listing every sensor with its unit, raw input, calibration id and whether it is simulated or calibrated](docs/assets/images/lab-overview-screenshot.png)

*The **Lab Overview** dashboard, provisioned out of the box and shown here
against the demo stack.*

The frontend **is** Grafana, not a wrapper around it, so everything
Grafana does is available immediately — labmon supplies the data and a
starting dashboard.

| What | Use in a lab | Docs |
|---|---|---|
| **Panels** | Time series, gauges, histograms, heatmaps, state timelines — built by clicking, not by writing plotting code | [Panels](https://grafana.com/docs/grafana/latest/panels-visualizations/) |
| **Explore** | Ad-hoc digging without touching a saved dashboard | [Explore](https://grafana.com/docs/grafana/latest/explore/) |
| **Alerting** | Email, Slack or Telegram when a reading leaves its range, or when a sensor goes silent | [Alerting](https://grafana.com/docs/grafana/latest/alerting/) |
| **Snapshots** | Freeze a dashboard and send the link — for a logbook entry or a group meeting | [Sharing](https://grafana.com/docs/grafana/latest/dashboards/share-dashboards-panels/) |
| **Annotations** | Mark the moment you opened a valve, so the event sits on the plot next to its consequence | [Annotations](https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/annotate-visualizations/) |
| **Variables** | One dashboard with a "which cryostat?" dropdown, instead of one per instrument | [Variables](https://grafana.com/docs/grafana/latest/dashboards/variables/) |

A dashboard you build in the browser can be exported as JSON and dropped
into `grafana/dashboards/`, which puts it under version control and brings
it back automatically on a fresh install.
[`docs/grafana.md`](docs/grafana.md) covers that, the query syntax for new
panels, and a few sharp edges.

## Connecting a real instrument

Two shapes of instrument, two paths.

### A sensor that outputs a voltage

It reaches labmon through a microcontroller — an Arduino Due is the
reference board — plugged into a USB port.

```
   sensor ──volts──►  ADC board  ──USB serial──►  serial-sensor  ──►  InfluxDB
                                                        ▲
                                                        │
                                                calibration.toml
                                          what a count means, per channel
```

The board only ever sends **raw ADC counts**. What a count *means* lives on
the computer, in a small text file, so recalibrating never means reflashing
the board:

```toml
[channels.A0]
sensor_id = "cryo-77k"
measurement = "temperature"
conversion_factor = "42.5 kelvin / volt"
```

Real sensors are rarely that obliging, so there are five conversion modes:

| Mode | For a response that is | You provide |
|---|---|---|
| `linear` | proportional | one dimensioned factor |
| `affine` | a straight line with an offset | factor and offset |
| `spline` | smoothly curved | measured points; a cubic is fitted |
| `piecewise_linear` | curved, with few points | measured points; straight segments between |
| `expression` | a known formula | e.g. `10**(1.667*v - 11.33)` |

Every conversion is checked and trial-applied when the file loads, so a
typo or a dimensional mistake fails immediately instead of recording wrong
numbers. Units are [pint](https://pint.readthedocs.io/en/stable/)
quantities throughout, so adding millibars to kelvin is an error, not a
number.

[`calibration.example.toml`](calibration.example.toml) works through all
five modes with realistic sensors.
[`docs/serial-sensor.md`](docs/serial-sensor.md) has the rest — the
reference Arduino sketch in
[`firmware/`](firmware/due_native_serial/due_native_serial.ino), giving the
board a stable device name, and testing the whole chain against a virtual
serial port before hardware arrives.

> [!WARNING]
> **Not yet verified on hardware.** The serial path is tested end to end
> against a virtual serial port, but never against a physical Arduino Due.
> Check readings against a known voltage before trusting them.

### An instrument with its own software

Plenty of instruments never expose a voltage: they ship a Python SDK, a
REST endpoint or a CLI that hands back a reading already in physical units.
There is nothing to calibrate, so they skip the ADC path entirely.

```
   instrument ──vendor API──►  your script  ──►  InfluxDB
```

Copy [`templates/custom-sensor/`](templates/custom-sensor/) and replace one
function with the vendor call. Batching, retries and shutdown are handled
for you, including retrying a read that raises, so an instrument having a
bad night does not stop the process. The template runs against a simulated
value first, so the plumbing can be proved before you write any instrument
code. See [`docs/custom-sensor.md`](docs/custom-sensor.md).

## Logs, next to the measurements

A trace tells you a reading stopped, never why. That lives in some
container's output, reachable only by running `docker compose logs` on the
right machine, with no history beyond what Docker still holds.

The `logs` profile adds two containers, both Grafana's own: **Loki** stores
log lines, **Alloy** collects them from every container and from systemd
units on the host. They land in the same Grafana as the measurements, with
a **Logs** dashboard provisioned alongside the overview.

```bash
COMPOSE_PROFILES=demo,logs docker compose up -d --wait
```

Lines are labelled with the **reading they describe**, not the container
that emitted them:

```logql
{sensor_id="cryo-77k"}
```

One process can report several channels — `serial-sensor` reads a
calibration file covering six of them and produces six labelled streams.
The label is read out of the line, so renaming a channel in a calibration
file changes it with no second copy to keep in step.

Client machines ship their logs to the same store, so one query covers the
whole lab. [`docs/logging.md`](docs/logging.md) has retention, log levels
and setup.

## Running it across the lab

Everything above runs on one machine. A real lab is usually three roles on
a small network:

```
   CLIENT MACHINE                              SERVER
   one per instrument                          one, always on
  ┌───────────────────────────┐               ┌────────────────────────────┐
  │  instrument               │               │  InfluxDB 3          :8181 │
  │      │                    │   readings    │  Grafana             :3000 │
  │      ▼                    │ ────────────► │  Loki  (logs profile)      │
  │  sensor script            │               │                            │
  │  Alloy (ships its logs) ──┼────────────►  │                            │
  └───────────────────────────┘               └─────────────┬──────────────┘
                                                            │
                                                            │  http://server:3000
                                                            ▼
                                              ┌────────────────────────────┐
                                              │  VIEWER — any browser      │
                                              │  nothing to install        │
                                              └────────────────────────────┘
```

| Role | Runs | Setup |
|---|---|---|
| **Server** | InfluxDB + Grafana, plus Loki and Alloy with the `logs` profile | [`docs/deployment.md`](docs/deployment.md) |
| **Client** | A sensor script, on whatever machine the instrument is wired to — a Raspberry Pi, a lab PC | [`docs/client-setup.md`](docs/client-setup.md) |
| **Viewer** | A browser | nothing |

A client can be a Docker container or a plain `uv tool install labmon`;
both are documented. A board can equally well be plugged straight into the server,
in which case there is no client machine at all.

### Opening the server to the network

A fresh install listens on **loopback only** — `127.0.0.1`, reachable only
from the machine running it. So the quickstart on a laptop publishes
nothing to the network around it. A server that clients push to opens up
with one setting in `.env`:

```bash
LABMON_BIND_ADDRESS=0.0.0.0      # every interface, so other machines can reach it
```

> [!CAUTION]
> Set a real `GRAFANA_ADMIN_PASSWORD` at the same time. `admin`/`admin` is
> fine while the port is only reachable from the machine itself; it is not
> once the port is open.

<details>
<summary><b>What about encryption?</b></summary>

<br>

Traffic is plain HTTP by default, which assumes a trusted lab network —
usually true for a subnet behind the institute's firewall, and worth
checking rather than assuming.

Where it does not hold, the `tls` profile puts a reverse proxy
([Caddy](https://caddyserver.com/)) with its own certificate authority in
front of InfluxDB, Grafana and Loki's push endpoint. Clients that have not
moved to it yet keep working. With the profile on, the plaintext ports
close, so the proxy is the only way in rather than an extra door beside one
still open.

[`docs/deployment.md`](docs/deployment.md#encrypting-client-and-viewer-traffic)
has the setup, including distributing the CA certificate to clients.

</details>

## How it works

A sensor script builds one data point per reading and hands it to a
queue-backed writer, which batches points and sends them from a background
thread:

```
  sampling loop  ──point──►  queue  ──►  writer thread  ──batch──►  InfluxDB
   never blocks              in RAM      retries with backoff
```

That indirection is why a sensor never stalls. A single write to InfluxDB
costs about a second, because the database waits until the data is safely
on disk before answering. Batching turns that into a millisecond per point,
and the queue means the sampling loop never waits for it at all — including
through an outage, which is retried with backoff instead of dropping
readings. [`docs/latency.md`](docs/latency.md) has the measurements behind
those numbers.

Grafana reads the database through its SQL (Flight SQL) mode;
[`docs/grafana.md`](docs/grafana.md) explains that choice and how the
datasource and dashboards are provisioned from files in this repository.

## Reference

### Configuration

[`docs/configuration.md`](docs/configuration.md) is the index: every
setting, what reads it, and where it lives.

### Starting over

`labmon reset-database` empties the database and creates it again, keeping
its retention period. The admin token is untouched, so sensors on other
machines keep writing without being visited.

```bash
labmon reset-database              # asks you to type the database name
labmon reset-database --yes        # for a script that means it
```

It exists because `docker compose down -v` does not do this: InfluxDB's
data is a bind mount rather than a named volume, so the alternative was
`rm -rf .influxdb3/data` with the stack stopped.
[`docs/deployment.md`](docs/deployment.md#setting-up-and-starting-over)
covers what a reset does to the data on disk, and why there is no command
to delete a narrower slice of it.

### Project structure

<details>
<summary><b>What is in each directory</b></summary>

<br>

- `src/labmon/` — application code
  - `influx.py` — shared InfluxDB client configuration
  - `writer.py` — queue-backed writer decoupling producers from InfluxDB I/O latency
  - `calibration.py` — turns raw ADC counts into dimensioned physical quantities
  - `sensors/` — sensor scripts (simulated, boards over serial, instruments behind a vendor API)
  - `cli/` — the `labmon` command line
- `templates/` — copyable starting points for sensors labmon does not ship
- `firmware/` — reference Arduino sketches
- `demo/` — stands in for a board so the demo runs the real acquisition path
- `grafana/` — provisioned datasources and dashboards
- `loki/` · `alloy/` — log storage and collection, behind the `logs` profile
- `caddy/` — reverse proxy config, behind the `tls` profile
- `deploy/` — systemd units for machines that cannot run the containers
- `tests/` — pytest suite
- `typings/` — local type stubs for untyped third-party dependencies
- `Dockerfile` — builds `labmon:latest`, the one image every service runs;
  not where instrument-specific dependencies go
- `docker-compose.yml` — InfluxDB, Grafana, demo sensors, optional logs and TLS
- `docker-compose.client.yml` — sensor-only compose file for a client machine
- `calibration.example.toml` — worked example of every conversion mode
- `monitor.example.toml` — worked example of the TUI tile config

</details>

### Development

Requires [uv](https://docs.astral.sh/uv/). The four checks CI enforces:

```bash
uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=100
uv run ruff check .
uv run basedpyright
uv run typos
```

Coverage is held at 100%, and basedpyright runs on its default (strictest)
preset with zero errors and zero warnings.
[`CONTRIBUTING.md`](CONTRIBUTING.md) has the reasoning and the workflow;
`AGENTS.md` is the condensed version of the same document.

```bash
uv run pre-commit install --allow-missing-config
```

runs the quick half of that list on every commit and the slow half on every
push, from the same lockfile CI uses. Optional, skippable with
`--no-verify`.

### Status

**Beta.** The feature set is settled and everything described here runs.
Interfaces may still move between minor versions; when one does, the
release notes say what to change.

The one gap: the serial acquisition path has never met a real Arduino Due.
It is tested end to end against a virtual serial port and against a feeder
speaking the firmware's wire format, but the last centimetre is unproven.
Everything else — storage, dashboards, networking between machines, log
collection, resilience — is exercised daily against live services, and by a
cold-start job that builds the whole stack from nothing on every push.

**1.0 means verified against real hardware, and running in at least two
different labs for at least six months.** That is deliberately slow.
Monitoring software is trusted by default once installed — nobody
re-derives whether the number on the dashboard is right — so the version
number should stay honest about how much has been proven outside one bench.

Planned work lives in
[issues](https://github.com/quentinmarolleau/labmon/issues), grouped into
[milestones](https://github.com/quentinmarolleau/labmon/milestones). Every
open issue carries a decision: `validated` is agreed and scheduled,
`waiting-for-need` is understood and wanted *if* a concrete case appears.

[`CHANGELOG.md`](CHANGELOG.md) records what changed in each release.

### Contributing, security, license

[`CONTRIBUTING.md`](CONTRIBUTING.md) has the branch and PR workflow, commit
conventions and testing policy.
[`SECURITY.md`](SECURITY.md) has the private disclosure route, the
supported version, and the three assumptions the design is built on.
Licensed GPLv3 — see [LICENSE](LICENSE).
