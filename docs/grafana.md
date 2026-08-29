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

The **Logs** dashboard sits beside it in the same folder, reading Loki
rather than InfluxDB: log volume by level, warning and error counts, the
warning and error lines themselves, readings skipped per sensor, and an
unparsed view of every container. It needs the `logs` profile — without
it the Loki datasource is provisioned but nothing is running behind it,
and every panel reads empty. See [`docs/logging.md`](logging.md).

## How the datasource is wired

`grafana/provisioning/datasources/influxdb3.yaml` provisions an `InfluxDB3`
datasource using Grafana's built-in InfluxDB data source in **SQL** mode,
which talks to InfluxDB 3's Flight SQL (gRPC) interface rather than the
legacy InfluxQL/Flux modes. Since this stack has no TLS between containers,
`insecureGrpc: true` tells Grafana not to expect it — and that stays true
under the `tls` profile, which encrypts the boundary rather than the
Docker network (see
[`docs/deployment.md`](deployment.md#what-is-encrypted-and-what-is-not)).
The auth token and
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

## What CI checks about a dashboard

`tests/test_dashboard.py` runs a handful of structural checks over every
file in `grafana/dashboards/` on every PR. They exist because Grafana's
failure mode for a malformed dashboard is silence: a query under the wrong
key, or a datasource uid nobody provisioned, renders "No data" and logs
nothing, so the dashboard keeps looking correct while showing you nothing.

A dashboard you add is picked up with no change to the test file.

The checks cover the mistakes these files have actually made — every data
panel carrying at least one target, a non-empty query under the key its own
backend reads (`rawSql` for InfluxDB, `expr` for Loki), no leftover `query`
twin, every datasource reference resolving to a uid provisioning actually
creates *and* agreeing with the type it is provisioned as, unique panel ids,
every `$variable` declared, custom variables carrying their values inline,
`xychart` panels declaring a `pluginVersion`, and any `*-panel` type (the
naming convention for community plugins) appearing in `GRAFANA_PLUGINS`.

Datasource references are checked wherever they appear — panels, targets and
template variables — because an optional datasource makes a particular
mistake easy: Loki only runs under the `logs` profile, so a dashboard
reading it can be committed while the provisioning file that backs it is
still unstaged. The result renders empty for everyone.

Adding a datasource of a new type needs one line in the test file, mapping
that type to the key it reads its query from. A type the suite does not know
fails rather than being skipped, so the check cannot be silently switched
off by using a backend nobody taught it about.

None of them run a query, so SQL that is structurally fine but names a
column the schema does not have passes here. Catching that needs a live
stack, not a JSON parse, which is what `scripts/smoke_dashboard.py` does:
it sends every panel target on every dashboard through Grafana's query
endpoint and counts the rows that come back.

Counting rows rather than settling for "no error" is what makes it worth
running against LogQL. A bad column name is at least a SQL error, but Loki
answers a renamed logfmt field, a `msg=` that matches nothing and a
mistyped label with an empty result and no complaint — so a query that has
stopped meaning anything is indistinguishable from a healthy one until the
rows are counted. Panels that filter on severity are exempt, since a run
that logged no warnings is a good run; everything else has to return rows,
and a new panel is held to that until someone exempts it by name.

Every dashboard means every profile that backs one has to be running, so
`--dashboard` narrows it to a subset. `scripts/smoke_logs.py` covers the
half neither reaches, proving collection gets to Loki at all.
