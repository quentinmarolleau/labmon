# Grafana

Visualizes data written to InfluxDB. Provisioned automatically — no manual
setup needed.

## Access

```
http://localhost:3000
```

Login: `admin` / `admin` (or `GRAFANA_ADMIN_PASSWORD` if set in `.env`).

The **Mock Temperature Sensor** dashboard (folder: `labmon`) shows every
sensor writing to the `temperature` table, one line per `sensor_id`,
auto-refreshing every 5 seconds.

## How the datasource is wired

`grafana/provisioning/datasources/influxdb3.yaml` provisions an `InfluxDB3`
datasource using Grafana's built-in InfluxDB data source in **SQL** mode,
which talks to InfluxDB 3's Flight SQL (gRPC) interface rather than the
legacy InfluxQL/Flux modes. Since this stack has no TLS between containers,
`insecureGrpc: true` tells Grafana not to expect it. The auth token is
injected from the `INFLUXDB3_AUTH_TOKEN` environment variable via
provisioning's `${VAR}` expansion — never hardcoded into the file.

## Writing your own panel query

The SQL mode's macros differ slightly from other Grafana SQL data sources —
notably there's no `$__timeFilter`. Use:

```sql
SELECT time, sensor_id, value
FROM temperature
WHERE $__timeFrom() <= time AND time <= $__timeTo()
ORDER BY time
```

`ORDER BY time` must be ascending — the panel's `time_series` format
rejects descending results. A result shaped as `(time, label_column,
numeric_column)` is automatically split into one series per distinct label
value (here, one line per `sensor_id`).

## Adding more dashboards

Drop a dashboard JSON file into `grafana/dashboards/`; the file provider
(`grafana/provisioning/dashboards/dashboards.yaml`) picks it up automatically
within `updateIntervalSeconds` (30s), no restart needed.
