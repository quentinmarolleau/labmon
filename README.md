# labmon

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
