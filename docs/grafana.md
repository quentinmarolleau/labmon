# Grafana

Visualizes data written to InfluxDB. Provisioned automatically — no manual
setup needed.

## Access

```
http://localhost:3000
```

Login: `admin` / `admin` (or `GRAFANA_ADMIN_PASSWORD` if set in `.env`).

The **Lab Overview** dashboard (folder: `labmon`) has three panels,
auto-refreshing every 5 seconds:

- **Room Temperature** — `room-1`/`room-2`, over the dashboard's default
  1h window.
- **Cryogenic Zone** — `cryo-77k`/`cryo-4k`, over its own 5min window
  (see the `timeFrom` panel override below).
- **Science Chamber Pressure** — a gauge showing the latest reading from
  `chamber-1` (mbar, ~1e-7), not a time series.

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

## Giving one panel its own time window

A panel can override the dashboard's time range independently by setting
`"timeFrom": "5m"` (a bare relative duration) directly on the panel JSON —
this is exactly how the **Cryogenic Zone** panel gets its own 5min window
while **Room Temperature** stays on the dashboard's 1h default. This only
works when the dashboard's own range is itself relative (e.g. `now-1h`),
which is the case here.

## Gauges and other "latest value" panels

A `gauge` panel wants the single most recent reading, not a time series —
using `$__timeFrom()`/`$__timeTo()` and the ascending-order requirement
described above doesn't apply here. Instead, use `format: "table"` (which
has no ordering requirement) with a query that fetches just the latest row:

```sql
SELECT time, value FROM pressure WHERE sensor_id = 'chamber-1' ORDER BY time DESC LIMIT 1
```

The **Science Chamber Pressure** gauge uses exactly this pattern.

## Adding more dashboards

Drop a dashboard JSON file into `grafana/dashboards/`; the file provider
(`grafana/provisioning/dashboards/dashboards.yaml`) picks it up automatically
within `updateIntervalSeconds` (30s), no restart needed.
