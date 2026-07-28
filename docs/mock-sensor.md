# Mock sensor

Simulates a sensor for development: generates readings via a
mean-reverting random walk around a setpoint and writes them to InfluxDB,
so downstream tooling (dashboards, alerting, other sensor scripts) can be
built without real hardware. Generalized enough to simulate several kinds
of sensors, not just temperature — see `--measurement`/`--field`/
`--log-scale` below.

For reading an actual board rather than simulating one, see
[`docs/serial-sensor.md`](serial-sensor.md).

## Usage

```bash
# Defaults: sensor-id "mock-sensor-1", measurement "temperature", setpoint
# 21, one reading every 5s
uv run mock-sensor

# A second room temperature sensor, sampled every second
uv run mock-sensor --sensor-id room-2 --setpoint 22 --interval 1 --unit "°C"

# A cryogenic zone sensor
uv run mock-sensor --sensor-id cryo-77k --setpoint 77 --noise 0.3 --unit K

# A vacuum gauge: values spanning orders of magnitude need --log-scale
# (see below) so noise/mean-reversion scale multiplicatively, not by a
# fixed absolute amount
uv run mock-sensor --sensor-id chamber-1 --measurement pressure \
  --setpoint 1e-7 --noise 0.05 --log-scale --unit mbar

# Full option list
uv run mock-sensor --help
```

Stop with Ctrl+C (or `docker stop`/`kill` if run as a service) — the
process closes its InfluxDB connection cleanly on SIGINT/SIGTERM.

## Options

| Flag             | Default        | Purpose                                                        |
|-------------------|----------------|------------------------------------------------------------------|
| `--sensor-id`      | `mock-sensor-1`| Tag identifying the sensor                                       |
| `--interval`       | `5.0`          | Seconds between readings                                          |
| `--setpoint`       | `21.0`         | Baseline reading the walk reverts toward                          |
| `--measurement`    | `temperature`  | InfluxDB measurement (table) to write to                          |
| `--field`          | `value`        | InfluxDB field name for the reading                                |
| `--noise`          | `0.1`          | Std dev of Gaussian noise added each step                         |
| `--log-scale`      | off            | Perform the walk in log10 space (see below)                       |
| `--unit`           | `""`           | Unit of the reading (e.g. `°C`, `K`, `mbar`) — written as an InfluxDB `unit` tag when set, and shown in the console output |

### `--log-scale`

The default (linear) walk adds noise of a fixed absolute size — fine for a
quantity like room temperature, but wrong for something like vacuum
pressure that can span many orders of magnitude: a noise stddev tuned for
`1e-7` would be meaningless at `1e-3`, and a large enough absolute noise
could push a reading below zero, which isn't physically possible for a
pressure. With `--log-scale`, the walk operates on `log10(reading)`
internally, so `--noise` becomes a proportional (log10) quantity — jitter
and mean-reversion scale with magnitude, and the reading can never go
non-positive. `--setpoint` stays in ordinary linear units either way.

### `--unit`

When set, `--unit` is written as an InfluxDB **tag** (`unit=...`), not a
field — units are metadata you filter/group by (e.g. picking a unit-aware
Grafana display, or distinguishing `K` from `°C` readings in the same
`temperature` measurement), not a measured value, so they belong in
InfluxDB's indexed tag space rather than mixed in with the numeric field
data. Left unset (the default), no `unit` tag is written at all.

## Configuration

Read from the environment (see `.env`, loaded automatically via direnv):

| Variable               | Default                 | Purpose                          |
|-------------------------|--------------------------|-----------------------------------|
| `INFLUXDB3_AUTH_TOKEN`  | *(required)*             | Auth token for the InfluxDB API   |
| `INFLUXDB_HOST`         | `http://localhost:8181`  | InfluxDB server URL               |
| `INFLUXDB_DATABASE`     | `lab`                    | Target database                   |

`INFLUXDB_DATABASE` is in `.env.example`, so it applies uniformly to a
host-side run, the containerized mock sensors, and Grafana's datasource.
`INFLUXDB_HOST` is deliberately left out of `.env.example`: the
containerized mock sensors need the Docker network hostname
(`http://influxdb:8181`, hardcoded in `docker-compose.yml`), while a
host-side run like this one needs `http://localhost:8181` (the default
above) — one `.env` value can't satisfy both, so set it inline for an
ad hoc host-side run only, e.g. `INFLUXDB_HOST=http://elsewhere:8181
uv run mock-sensor ...`.

## Inspecting written data

Via the InfluxDB3 CLI, inside the running container:

```bash
docker compose exec influxdb influxdb3 query \
  --token "$INFLUXDB3_AUTH_TOKEN" \
  --database lab \
  "SELECT * FROM temperature ORDER BY time DESC LIMIT 20"
```

Or list what tables/measurements exist:

```bash
docker compose exec influxdb influxdb3 query \
  --token "$INFLUXDB3_AUTH_TOKEN" \
  --database lab \
  "SHOW TABLES"
```
