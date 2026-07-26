# labmon

[![License: GPLv3](https://img.shields.io/github/license/quentinmarolleau/labmon)](LICENSE)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-blue)](pyproject.toml)
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

## Project structure

- `src/labmon/` — application code
  - `influx.py` — shared InfluxDB client configuration
  - `writer.py` — queue-backed writer decoupling producers from InfluxDB I/O latency
  - `sensors/` — sensor scripts (mock and, later, real)
- `tests/` — pytest suite
- `docs/` — usage docs per component
- `typings/` — local type stubs for untyped third-party dependencies
- `docker-compose.yml` — local InfluxDB instance

## Development

```bash
uv run pytest
uv run ruff check .
uv run basedpyright
uv run typos
```

See [`docs/mock-temperature-sensor.md`](docs/mock-temperature-sensor.md)
for sensor usage and how to inspect the data it writes.

## License

GPLv3 — see [LICENSE](LICENSE).
