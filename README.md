# labmon

[![License: GPLv3](https://img.shields.io/github/license/quentinmarolleau/labmon)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![codecov](https://codecov.io/gh/quentinmarolleau/labmon/branch/main/graph/badge.svg)](https://codecov.io/gh/quentinmarolleau/labmon)
[![Repo size](https://img.shields.io/github/repo-size/quentinmarolleau/labmon)](https://github.com/quentinmarolleau/labmon)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/quentinmarolleau/labmon/pulls)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Maturity: alpha](https://img.shields.io/badge/maturity-alpha-orange)](https://github.com/quentinmarolleau/labmon)

Flexible laboratory monitoring system, built on InfluxDB 3.

## Quickstart

```bash
cp .env.example .env   # fill in INFLUXDB_NODE_ID and INFLUXDB3_AUTH_TOKEN
docker compose up -d --wait
uv sync
uv run mock-temperature-sensor
```

Then open [Grafana](http://localhost:3000) (`admin`/`admin`) to watch the
data live — see [`docs/grafana.md`](docs/grafana.md).

## Architecture

![labmon architecture: sensors and edge devices feed InfluxDB 3, which Grafana queries for dashboards](docs/assets/images/diagram.png)

This is the target architecture — microcontrollers and edge devices
(Arduino, Raspberry Pi, etc.) feeding InfluxDB directly aren't built yet.
What's implemented today: sensor scripts run on the host (via `uv run`)
and write into InfluxDB through a queue-backed writer, so producers are
never blocked by write latency. InfluxDB and Grafana run as containers
managed by `docker-compose.yml`. Grafana queries InfluxDB via its SQL
(Flight SQL) mode — see [`docs/grafana.md`](docs/grafana.md) for why that
mode specifically, and its macro quirks.

## Project structure

- `src/labmon/` — application code
  - `influx.py` — shared InfluxDB client configuration
  - `writer.py` — queue-backed writer decoupling producers from InfluxDB I/O latency
  - `sensors/` — sensor scripts (mock and, later, real)
- `tests/` — pytest suite
- `docs/` — usage docs per component
- `typings/` — local type stubs for untyped third-party dependencies
- `grafana/` — provisioned datasource and dashboards
- `docker-compose.yml` — local InfluxDB and Grafana instances
- `CONTRIBUTING.md` / `AGENTS.md` — workflow, commit conventions, testing policy

## Development

```bash
uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=100
uv run ruff check .
uv run basedpyright
uv run typos
```

See [`docs/mock-temperature-sensor.md`](docs/mock-temperature-sensor.md)
for sensor usage and how to inspect the data it writes.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the branch/PR workflow,
commit conventions, and testing policy (`AGENTS.md` is the condensed,
agent-oriented version of the same document).

## License

GPLv3 — see [LICENSE](LICENSE).
