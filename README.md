<h1 align="center">
  <img alt="labmon" src="docs/assets/images/header.png" width="420">
</h1>

<p align="center">
  <a href="LICENSE"><img alt="License: GPLv3" src="https://img.shields.io/github/license/quentinmarolleau/labmon"></a>
  <a href="https://github.com/quentinmarolleau/labmon/pulls"><img alt="PRs welcome" src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg"></a>
  <a href="https://github.com/quentinmarolleau/labmon"><img alt="Repo size" src="https://img.shields.io/github/repo-size/quentinmarolleau/labmon"></a>
  <a href="pyproject.toml"><img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-blue"></a>
  <br>
  <a href="https://github.com/quentinmarolleau/labmon/actions/workflows/ci.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/quentinmarolleau/labmon/ci.yml?branch=main&amp;label=tests&amp;job=test%20%283.14%29"></a>
  <a href="https://codecov.io/gh/quentinmarolleau/labmon"><img alt="codecov" src="https://codecov.io/gh/quentinmarolleau/labmon/branch/main/graph/badge.svg"></a>
  <a href="https://github.com/DetachHead/basedpyright"><img alt="basedpyright" src="https://img.shields.io/github/actions/workflow/status/quentinmarolleau/labmon/ci.yml?branch=main&amp;label=basedpyright&amp;job=typecheck"></a>
  <a href="https://github.com/astral-sh/ruff"><img alt="Ruff" src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json"></a>
  <a href="https://github.com/quentinmarolleau/labmon"><img alt="Maturity: alpha" src="https://img.shields.io/badge/maturity-alpha-orange"></a>
</p>

<p align="center">
  <strong>Know what your lab is doing — right now, and last Tuesday at 3 a.m.</strong>
</p>

labmon records whatever your experiment produces (cryostat temperature,
chamber pressure, laser power, a magnet's current) into a time-series
database, and puts it on a live dashboard anyone in the room can open in
a browser. Two commands to start, no database or web experience needed.

![The Lab Overview dashboard: a row of current-value tiles for cold finger, chamber pressure, laser power and bias rail; a panel plotting a calibrated temperature against the raw ADC voltage it came from; cryogenic and room temperature, vacuum and laser power time series; a needle dial for laser detuning; an XY plot of beam position; and a table listing every sensor with its unit, raw input, calibration id and whether it is simulated or calibrated](docs/assets/images/lab-overview-screenshot.png)

*The **Lab Overview** dashboard you get out of the box, running against
the demo stack that starts automatically — an example of what can be put
together visually.*

## Contents

- [What labmon gives you](#what-labmon-gives-you)
- [Quickstart](#quickstart) — running in two minutes, no hardware needed
- [The dashboard](#the-dashboard) — what Grafana brings along
- [Connecting a real instrument](#connecting-a-real-instrument)
- [Running it across the lab](#running-it-across-the-lab)
- [How it works](#how-it-works)
- [Project structure](#project-structure)
- [Development](#development)
- [Status](#status)
- [Contributing](#contributing) · [License](#license)

## What labmon gives you

- **Live plots of anything.** Point a sensor at it, and the reading shows
  up on a dashboard that refreshes itself.
- **A permanent record.** Every reading is kept with its timestamp, and
  stays explorable in the future.
- **Physical units, checked.** Calibration lives in a small serialized file:
  say that a channel reads `42.5 kelvin / volt` and labmon derives the
  result in kelvin — [dimensional analysis](https://pint.readthedocs.io/en/stable/)
  catches a mistake like adding millibars to kelvin before any data is
  recorded.
- **Alerts when something drifts.** Grafana can email or message you when
  a reading leaves the range you expect.
- **One dashboard for the whole room.** The database and dashboard run on
  one machine; instruments elsewhere in the lab push to it over the
  network, and anyone opens it in a browser.
- **Nothing to babysit.** Sensors keep sampling through a network hiccup
  or a server restart and catch up afterwards, rather than losing
  readings or dying.

Under the hood it is [InfluxDB 3](https://docs.influxdata.com/influxdb3/core/)
for storage and [Grafana](https://grafana.com/docs/grafana/latest/) for
visualization, wired together and pre-configured so neither needs setting
up by hand.

## Quickstart

You need [Docker](https://docs.docker.com/get-started/get-docker/). No
sensors, no hardware, nothing else.

```bash
cp .env.example .env
```

Set `INFLUXDB_NODE_ID` to any name you like (`node0` is fine). The auth token
has to come from InfluxDB itself, so start it on its own first and ask it for
one:

```bash
docker compose up -d --wait influxdb
docker compose exec influxdb influxdb3 create token --admin
```

Put that token in `.env` as `INFLUXDB3_AUTH_TOKEN` — it is shown once and
only one admin token exists per instance, so keep it. Then start everything:

```bash
docker compose up -d --wait
```

Open [http://localhost:3000](http://localhost:3000) and log in with
`admin` / `admin`.

Two things start writing immediately, so the dashboard is alive before
you have any hardware:

- **Five simulated sensors** — two room thermometers, two cryogenic
  sensors and a vacuum gauge — inventing readings already in physical
  units. See [`docs/mock-sensor.md`](docs/mock-sensor.md) to add more or
  change what they simulate.
- **Four calibrated channels** running the *real* acquisition path: raw
  ADC counts stream over TCP in the firmware's wire format, and
  `serial-sensor` converts them exactly as it would for a board on a
  serial port. Nothing is mocked but the board itself — see
  [`docs/demo-stack.md`](docs/demo-stack.md).

That second half is what the dashboard's **Calibration layer** panel
draws: the voltage the ADC saw, next to the physical quantity the
calibration file turned it into. [`docs/grafana.md`](docs/grafana.md)
covers what the rest of the dashboard is made of.

Turning the simulated sensors off (for a real deployment) is a single
setting — see [`docs/deployment.md`](docs/deployment.md#demo-vs-server-compose_profiles).

## The dashboard

The front end **is** Grafana, not a thin imitation of it, so everything
Grafana can do is available immediately — labmon only supplies the data
and a starting dashboard. The parts most worth knowing about:

| What | Why it matters in a lab | Grafana docs |
|---|---|---|
| **Panels and visualizations** | Time series, gauges, histograms, heatmaps, state timelines — built by clicking, not by writing plotting code | [Panels and visualizations](https://grafana.com/docs/grafana/latest/panels-visualizations/) |
| **Explore** | Ad-hoc digging through the data without touching a saved dashboard — the "what actually happened at 03:14?" tool | [Explore](https://grafana.com/docs/grafana/latest/explore/) |
| **Alerting** | Notify by email, Slack, Telegram… when a reading leaves its expected range, or when a sensor goes silent | [Alerting](https://grafana.com/docs/grafana/latest/alerting/) |
| **Snapshots and sharing** | Freeze a dashboard as it looks now and send the link — useful for a logbook entry, a group meeting, or a paper draft | [Share dashboards and panels](https://grafana.com/docs/grafana/latest/dashboards/share-dashboards-panels/) |
| **Annotations** | Mark the moment you opened a valve or changed a setpoint, so the event sits on the plot next to its consequence | [Annotate visualizations](https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/annotate-visualizations/) |
| **Variables** | One dashboard with a dropdown for "which cryostat?", instead of one dashboard per instrument | [Variables](https://grafana.com/docs/grafana/latest/dashboards/variables/) |

Dashboards you build in the browser can be exported as JSON and dropped
into `grafana/dashboards/` to become part of the repository — so a
dashboard someone perfected is version-controlled and comes back
automatically on a fresh install, rather than living only in one
browser's memory. [`docs/grafana.md`](docs/grafana.md) covers that, plus
the query syntax for new panels and a few sharp edges worth knowing.

## Connecting a real instrument

A sensor that outputs a voltage reaches labmon through a microcontroller
(an Arduino Due is the reference board) plugged into a USB port:

```
sensor ──voltage──► board's ADC ──USB serial──► serial-sensor ──► InfluxDB ──► Grafana
                                                     │
                                            calibration.toml
                                     (what a voltage means, per channel)
```

The board only ever sends **raw ADC counts**. What a count *means* lives
on the computer, in a small text file — so recalibrating never means
reflashing the board:

```toml
[channels.A0]
sensor_id = "cryo-77k"
measurement = "temperature"
conversion_factor = "42.5 kelvin / volt"
```

Real sensors are rarely that obliging, so five conversion modes are
available:

| Mode | For a response that is | You provide |
|---|---|---|
| `linear` | proportional | one dimensioned factor |
| `affine` | a straight line with an offset | factor and offset |
| `spline` | smoothly curved | measured points; a cubic is fitted |
| `piecewise_linear` | curved, with few points | measured points; straight segments between them |
| `expression` | a known formula | e.g. `10**(1.667*v - 11.33)` |

Every conversion is checked and trial-applied when the file loads, so a
typo or a dimensional mistake fails immediately with a clear message
rather than silently recording wrong numbers.

Start from [`calibration.example.toml`](calibration.example.toml), which
works through all five modes with realistic sensors, and read
[`docs/serial-sensor.md`](docs/serial-sensor.md) for the full picture —
including the reference Arduino sketch in
[`firmware/`](firmware/due_native_serial/due_native_serial.ino), giving
the board a stable device name, and testing the whole chain with a
virtual serial port before any hardware arrives.

> **Not yet verified on hardware.** The serial acquisition path has been
> tested end to end against a virtual serial port, but never against a
> physical Arduino Due. Check readings against a known voltage before
> trusting them.

### Instruments with their own software

Plenty of instruments never expose a voltage: they come with a
manufacturer's Python SDK, a REST endpoint or a CLI that hands back a
reading already in physical units. There is nothing to calibrate, so those
skip the ADC path entirely.

```
instrument ──vendor API──► your script ──► InfluxDB ──► Grafana
```

Copy [`templates/custom-sensor/`](templates/custom-sensor/), replace one
function with the vendor call, and the batching, retries and shutdown are
handled for you — including retrying a read that raises, so an instrument
having a bad night does not stop the process. The template runs against a
simulated value before you write any instrument code, so the plumbing can
be proved first. See [`docs/custom-sensor.md`](docs/custom-sensor.md).

## Running it across the lab

Everything above runs on one machine. A real lab is usually three roles
on a small network:

| Role | Runs | Setup |
|---|---|---|
| **Server** | InfluxDB + Grafana, on one always-on machine | [`docs/deployment.md`](docs/deployment.md) |
| **Clients** | A sensor script, on whatever machine the instrument is wired to (a Raspberry Pi, a lab PC) | [`docs/client-setup.md`](docs/client-setup.md) |
| **Viewers** | Just a browser | nothing to install |

A client can be a Docker container or a plain Python install — both are
documented, and a board can equally well be plugged straight into the
server, in which case there is no client machine at all.

Traffic is plain HTTP today, deliberately: it assumes a trusted lab
network, and the reasoning (plus what changes if that stops being true)
is written down in
[`docs/deployment.md`](docs/deployment.md#security-plain-http-by-design-for-now).

## How it works

![labmon architecture: sensors and edge devices feed InfluxDB 3, which Grafana queries for dashboards](docs/assets/images/diagram.png)

Sensor scripts build one data point per reading and hand it to a
queue-backed writer, which batches points and sends them from a
background thread. That indirection is the reason a sensor never stalls:
a single write to InfluxDB costs about a second, because the database
waits until the data is safely on disk before answering. Batching turns
that into a millisecond per point, and the queue means the sampling loop
never waits for it at all — including through an outage, which is
retried with backoff instead of dropping data.
[`docs/latency.md`](docs/latency.md) has the measurements behind every
number in that paragraph.

Grafana reads the database through its SQL (Flight SQL) mode;
[`docs/grafana.md`](docs/grafana.md) explains that choice and how the
datasource and dashboards are provisioned automatically from files in
this repository.

## Project structure

- `src/labmon/` — application code
  - `influx.py` — shared InfluxDB client configuration
  - `writer.py` — queue-backed writer decoupling producers from InfluxDB I/O latency
  - `calibration.py` — turns raw ADC counts into dimensioned physical quantities
  - `sensors/` — sensor scripts (simulated, boards over serial, and instruments behind a vendor API)
- `templates/` — copyable starting points for sensors labmon does not ship
- `firmware/` — reference Arduino sketches for boards labmon reads
- `demo/` — stands in for a board so the demo runs the real acquisition path
- `tests/` — pytest suite
- `docs/` — usage docs per component
- `typings/` — local type stubs for untyped third-party dependencies
- `grafana/` — provisioned datasource and dashboards
- `Dockerfile` — builds labmon's own code for the containerized sensors
- `docker-compose.yml` — local InfluxDB, Grafana, and demo sensor instances
- `docker-compose.client.yml` — sensor-only compose file for a remote client machine
- `calibration.example.toml` — worked example of every sensor conversion mode
- `deploy/` — systemd units and docs for machines that can't run the containers
- `CONTRIBUTING.md` / `AGENTS.md` — workflow, commit conventions, testing policy
- `BACKLOG.md` — planned/considered future work, roughly prioritized

## Development

Requires [uv](https://docs.astral.sh/uv/). The four checks CI enforces:

```bash
uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=100
uv run ruff check .
uv run basedpyright
uv run typos
```

Test coverage is held at 100%, and basedpyright runs on its default
(strictest) preset with zero errors and zero warnings — see
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the reasoning and the workflow.

## Status

Alpha, and honest about it: the software is tested and running, but the
serial acquisition path has never met a real Arduino Due. Everything
else — storage, dashboards, networking between machines, resilience — is
exercised daily against live services.

[`BACKLOG.md`](BACKLOG.md) tracks what is planned next, with effort
estimates and a suggested order. Near the top: a proper documentation
site, and TLS for deployments beyond a trusted LAN.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the branch/PR workflow,
commit conventions, and testing policy (`AGENTS.md` is the condensed,
agent-oriented version of the same document).

## License

GPLv3 — see [LICENSE](LICENSE).
