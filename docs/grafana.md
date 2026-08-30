# Grafana

The browser frontend. Datasources and dashboards are provisioned from
files in this repository, so there is no manual setup.

```
   Grafana  ──Flight SQL (gRPC)──►  InfluxDB 3     readings
        │
        └────────HTTP─────────────►  Loki          log lines (logs profile)
```

## Getting in

```
http://localhost:3000
```

`admin` / `admin`, or `GRAFANA_ADMIN_PASSWORD` if set in `.env`.

Two dashboards are provisioned, both in the `labmon` folder.

**Lab Overview** auto-refreshes every 5 seconds over a 15 minute window,
in four bands:

| Band | Panels |
|---|---|
| Current values | Cold finger, chamber pressure, laser power, bias rail |
| The conversion itself | Calibration layer (follows the channel dropdown), Cryogenic zone |
| Instruments | Laser detuning, Beam position, Vacuum |
| Everything else | Laser power, Room temperature, inventory table |

Two dropdowns drive it: **Calibrated channel** repoints the Calibration
layer panel at any of the six calibrated channels, and **Rooms** picks which
room thermometers to plot.

**Logs** sits beside it, reading Loki rather than InfluxDB: log volume by
level, warning and error counts, the warning and error lines themselves,
readings skipped per sensor, and an unparsed view of every container.

> [!NOTE]
> The Logs dashboard needs the `logs` profile. Without it the Loki
> datasource is still provisioned but nothing runs behind it, so every panel
> reads empty. See [`logging.md`](logging.md).

What the Lab Overview is *showing* is covered in
[`demo-stack.md`](demo-stack.md). The rest of this page is about writing
panels of your own.

## Writing your own panel query

InfluxDB 3 is queried with **SQL**, through Grafana's built-in InfluxDB
datasource in SQL mode. The macros differ slightly from other Grafana SQL
datasources — notably there is no `$__timeFilter`:

```sql
SELECT time, sensor_id, value
FROM temperature
WHERE $__timeFrom() <= time AND time <= $__timeTo()
ORDER BY time
```

A few rules that are not obvious from the editor:

- **`ORDER BY time` must be ascending.** The `time_series` format rejects
  descending results.
- **A result shaped `(time, label_column, numeric_column)` splits itself**
  into one series per distinct label value. That is how **Cryogenic zone**,
  **Vacuum** and **Room temperature** each draw several sensors from one
  query.
- **The query goes in the target's `rawSql`.** A `query` field alongside it
  is ignored; SQL mode reads `rawSql` on both the backend and in the editor.

### "Latest value" panels

A panel that wants the single most recent reading — a stat tile, a gauge,
the marker showing where the beam is *now* — is not bound by the ascending
rule. Use `format: "table"`, which has no ordering requirement:

```sql
SELECT time, value FROM pressure WHERE sensor_id = 'chamber-1' ORDER BY time DESC LIMIT 1
```

The stat tiles take the other route: they select the whole window and let
`reduceOptions.calcs: ["lastNotNull"]` pick the last point, which is what
lets them draw a sparkline behind the number.

<details>
<summary><b>Units and scientific notation on a stat panel</b></summary>

<br>

A field's `unit` is the only place a stat panel can put a unit, and the two
obvious choices behave differently than they look:

- `unit: "sci"` gives exact scientific notation via `toExponential`, but
  returns no suffix at all — the number appears bare, with no unit.
- `unit: "suffix:mbar"` routes through `toFixed`, which returns
  `String(rounded)` directly whenever that string contains an exponent.

So the two *do* combine, but only with `decimals` left unset: auto decimals
picks enough places for small values that the string comes out exponential,
giving `9.91e-10 mbar`. *Setting* `decimals` defeats it — at 2, a value of
9.9e-8 rounds to `0.00 mbar`.

The trade-off is that trailing zeros are dropped, so the mantissa is
sometimes shown to fewer digits. Where a steady width matters more than the
unit, use `sci` and put the unit in the panel title.

A table column has no equivalent escape: it carries one `decimals` for every
row, so a column mixing magnitudes has to be formatted in SQL. The inventory
panel does this — see
[`demo-stack.md`](demo-stack.md#grafana-wrinkles-worth-knowing-if-you-edit-the-dashboard).

</details>

<details>
<summary><b>Giving one panel its own time window</b></summary>

<br>

Set `"timeFrom": "5m"` — a bare relative duration — directly on the panel
JSON. It only works when the dashboard's own range is itself relative (e.g.
`now-15m`), which is the case here. No panel currently uses it.

</details>

## Adding a dashboard

Drop a dashboard JSON file into `grafana/dashboards/`. The file provider
(`grafana/provisioning/dashboards/dashboards.yaml`) picks it up within
`updateIntervalSeconds` (30s), no restart needed. So a dashboard you built
by clicking around can be exported, committed, and it comes back on a fresh
install.

> [!IMPORTANT]
> Set `schemaVersion` to the version Grafana targets — **42**, the final
> version of the v1 dashboard API. A lower number makes Grafana run its
> migration chain over the file on every load, and those migrations rewrite
> panels.

## How the datasource is wired

`grafana/provisioning/datasources/influxdb3.yaml` provisions an `InfluxDB3`
datasource in **SQL** mode, which talks to InfluxDB 3's Flight SQL (gRPC)
interface rather than the legacy InfluxQL/Flux modes.

`insecureGrpc: true` tells Grafana not to expect TLS between containers.
That stays true under the `tls` profile, which encrypts the boundary rather
than the Docker network — see [what is encrypted, and what is
not](deployment.md#what-is-encrypted-and-what-is-not).

The token and database name are injected from `INFLUXDB3_AUTH_TOKEN` and
`INFLUXDB_DATABASE` through provisioning's `${VAR}` expansion, never
hardcoded. That expansion applies to any field, not only `secureJsonData`.
`docker-compose.yml` passes both through to the `grafana` service with a
`:-lab` fallback, so `INFLUXDB_DATABASE` is always set even when `.env` does
not define it.

## What CI checks about a dashboard

Grafana's failure mode for a malformed dashboard is **silence**: a query
under the wrong key, or a datasource uid nobody provisioned, renders "No
data" and logs nothing, so the dashboard keeps looking correct while showing
you nothing. Two layers guard against that, and a dashboard you add is
picked up by both with no change to either.

**`tests/test_dashboard.py`** parses every file in `grafana/dashboards/` on
every PR, checking the mistakes these files have actually made:

- every data panel carries at least one target
- a non-empty query under the key its own backend reads — `rawSql` for
  InfluxDB, `expr` for Loki — with no leftover `query` twin
- every datasource reference resolves to a uid provisioning creates, *and*
  agrees with the type it is provisioned as
- unique panel ids, every `$variable` declared, custom variables carrying
  their values inline
- `xychart` panels declare a `pluginVersion`, and any `*-panel` type (the
  naming convention for community plugins) appears in `GRAFANA_PLUGINS`

<details>
<summary><b>Why datasource references are checked everywhere they appear</b></summary>

<br>

Panels, targets and template variables all carry them, and an optional
datasource makes one mistake easy: Loki only runs under the `logs` profile,
so a dashboard reading it can be committed while the provisioning file that
backs it is still unstaged. The result renders empty for everyone.

Adding a datasource of a new type needs one line in the test file, mapping
that type to the key it reads its query from. A type the suite does not know
fails rather than being skipped, so the check cannot be switched off by
using a backend nobody taught it about.

</details>

**`scripts/smoke_dashboard.py`** runs against a live stack, because a JSON
parse cannot catch SQL that is structurally fine but names a column the
schema does not have. It sends every panel target on every dashboard through
Grafana's query endpoint and **counts the rows** that come back.

Counting rows rather than settling for "no error" is what makes it worth
running against LogQL. A bad column name is at least a SQL error, but Loki
answers a renamed logfmt field, a `msg=` matching nothing, and a mistyped
label with an empty result and no complaint — so a query that has stopped
meaning anything is indistinguishable from a healthy one until the rows are
counted.

Panels filtering on severity are exempt, since a run that logged no warnings
is a good run. Everything else has to return rows, and a new panel is held
to that until someone exempts it by name. Every dashboard means every
profile behind one has to be running, so `--dashboard` narrows it to a
subset. `scripts/smoke_logs.py` covers the half neither reaches, proving
collection gets to Loki at all.
