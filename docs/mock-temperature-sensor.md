# Mock temperature sensor

Simulates a temperature sensor for development: generates readings via a
mean-reverting random walk around a setpoint and writes them to InfluxDB,
so downstream tooling (dashboards, alerting, other sensor scripts) can be
built without real hardware.

## Usage

```bash
# Defaults: sensor-id "mock-temp-1", setpoint 21°C, one reading every 5s
uv run mock-temperature-sensor

# Simulate a fridge probe, sampled every second
uv run mock-temperature-sensor --sensor-id fridge-2 --setpoint 4 --interval 1

# Full option list
uv run mock-temperature-sensor --help
```

Stop with Ctrl+C (or `docker stop`/`kill` if run as a service) — the
process closes its InfluxDB connection cleanly on SIGINT/SIGTERM.

## Configuration

Read from the environment (see `.env`, loaded automatically via direnv):

| Variable               | Default                 | Purpose                          |
|-------------------------|--------------------------|-----------------------------------|
| `INFLUXDB3_AUTH_TOKEN`  | *(required)*             | Auth token for the InfluxDB API   |
| `INFLUXDB_HOST`         | `http://localhost:8181`  | InfluxDB server URL               |
| `INFLUXDB_DATABASE`     | `lab`                    | Target database                   |

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
