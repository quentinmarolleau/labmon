# Grafana

Visualizes data written to InfluxDB. Provisioned automatically — no manual
setup needed.

## Access

```
http://localhost:3000
```

Login: `admin` / `admin` (or `GRAFANA_ADMIN_PASSWORD` if set in `.env`).

The **Lab Overview** dashboard (folder: `labmon`) auto-refreshes every 5
seconds over a 15 minute window, and is laid out in four bands:

| Band | Panels |
|---|---|
| Current values | Cold finger, chamber pressure, laser power, bias rail |
| The conversion itself | Calibration layer (follows the channel dropdown), Cryogenic zone |
| Instruments | Laser detuning, Beam position, Vacuum |
| Everything else | Laser power, Room temperature, and an inventory table |

Two dropdowns drive it: **Calibrated channel** repoints the Calibration
layer panel at any of the six calibrated channels, and **Rooms** picks
which room thermometers to plot.

What the dashboard is *showing* is covered in
[`docs/demo-stack.md`](demo-stack.md), which also collects the Grafana
constraints worth knowing before editing any of these panels. The rest of
this page is about writing panels of your own.

## How the datasource is wired

`grafana/provisioning/datasources/influxdb3.yaml` provisions an `InfluxDB3`
datasource using Grafana's built-in InfluxDB data source in **SQL** mode,
which talks to InfluxDB 3's Flight SQL (gRPC) interface rather than the
legacy InfluxQL/Flux modes. Since this stack has no TLS between containers,
`insecureGrpc: true` tells Grafana not to expect it. The auth token and
database name are injected from the `INFLUXDB3_AUTH_TOKEN` and
`INFLUXDB_DATABASE` environment variables via provisioning's `${VAR}`
expansion (confirmed against Grafana's provisioning source — this
expansion applies to any field, not just `secureJsonData`) — never
hardcoded into the file. `docker-compose.yml` passes both through to the
`grafana` service with a `:-lab` fallback, so `INFLUXDB_DATABASE` is always
set even if `.env` doesn't define it.

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
value, which is how **Cryogenic zone**, **Vacuum** and **Room
temperature** each draw several sensors from one query.

The query goes in the target's `rawSql`. A `query` field alongside it is
ignored: SQL mode reads `rawSql` on both the backend and in the editor.

## Giving one panel its own time window

A panel can override the dashboard's time range independently by setting
`"timeFrom": "5m"` (a bare relative duration) directly on the panel JSON.
This only works when the dashboard's own range is itself relative (e.g.
`now-15m`), which is the case here. No panel currently uses it.

## "Latest value" panels

A panel that wants the single most recent reading rather than a series —
a stat tile, a gauge, or the marker showing where the beam is *now* — is
not bound by the ascending-order rule above. Use `format: "table"`, which
has no ordering requirement, with a query that fetches just the latest
row:

```sql
SELECT time, value FROM pressure WHERE sensor_id = 'chamber-1' ORDER BY time DESC LIMIT 1
```

The stat tiles take the other route: they select the whole window and let
`reduceOptions.calcs: ["lastNotNull"]` pick the last point, which is what
lets them draw a sparkline behind the number.

### Units and scientific notation

A field's `unit` is the only place a stat panel can put a unit, and the
two obvious choices behave differently than they look:

- `unit: "sci"` gives exact scientific notation via `toExponential`, but
  returns no suffix at all — the number appears bare, with no unit.
- `unit: "suffix:mbar"` routes through `toFixed`, which returns
  `String(rounded)` directly whenever that string contains an exponent.

So the two *do* combine, but only with `decimals` left unset: auto
decimals picks enough decimal places for small values that the string
comes out exponential, giving `9.91e-10 mbar`. *Setting* `decimals`
defeats it — at 2, a value of 9.9e-8 rounds to `0.00 mbar`.

The trade-off is that trailing zeros are dropped, so the mantissa is
sometimes shown to fewer digits. Where a steady width matters more than
the unit, use `sci` and put the unit in the panel title instead.

A table column has no equivalent escape: it carries one `decimals` for
every row, so a column mixing magnitudes has to be formatted in SQL. The
inventory panel does this — see
[`docs/demo-stack.md`](demo-stack.md#grafana-wrinkles-worth-knowing-if-you-edit-the-dashboard).

## Adding more dashboards

Drop a dashboard JSON file into `grafana/dashboards/`; the file provider
(`grafana/provisioning/dashboards/dashboards.yaml`) picks it up automatically
within `updateIntervalSeconds` (30s), no restart needed.

Set `schemaVersion` to the version Grafana targets (42 — the final version
of the v1 dashboard API). A lower number makes Grafana run its migration
chain over the file on every load, and those migrations rewrite panels.
