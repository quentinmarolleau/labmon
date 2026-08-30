# The demo stack

`COMPOSE_PROFILES=demo` starts sensors alongside InfluxDB and Grafana so
the dashboard has something to show before any hardware exists.

```bash
COMPOSE_PROFILES=demo docker compose up -d --build --wait
```

## Two acquisition paths, one database

```
  mock-room-1, mock-room-2, mock-cryo-77k,
  mock-cryo-4k, mock-pressure, mock-wavemeter ───────────────┐
      six processes inventing physical values                │
                                                             ▼
  demo-adc-feeder ──raw counts over TCP──► demo-serial-sensor ──► InfluxDB
   stands in for the board                  the real serial-sensor
```

| | What it runs | What it writes |
|---|---|---|
| `mock-*` (6 services) | `mock-sensor` | A value already in physical units |
| `demo-adc-feeder` + `demo-serial-sensor` | `serial-sensor` | `value`, `input_volts`, `calibration_id` |

The mock sensors invent a reading directly. Useful for filling a dashboard,
but they skip the conversion layer entirely, so nothing they write can
demonstrate it.

The second pair skips nothing. `demo-adc-feeder` streams raw ADC counts in
the wire format
[`firmware/due_native_serial`](../firmware/due_native_serial/due_native_serial.ino)
emits, and `demo-serial-sensor` is the ordinary `serial-sensor` entry point
reading them. Parsing, calibration, unit derivation, provenance — all the
real code. Only the board is substituted.

## How it reaches serial-sensor without a serial port

The feeder listens on TCP, and `serial-sensor` opens it with a pyserial
URL instead of a device path:

```yaml
command:
  - --port=socket://demo-adc-feeder:5555
  - --calibration=/app/demo/calibration.demo.toml
```

`--port` accepts anything pyserial's `serial_for_url` understands, so no
pty, no `socat` and no device passthrough is involved.

> [!TIP]
> That has a use beyond the demo: `rfc2217://host:port` reaches a board
> plugged into a serial device server elsewhere on the network rather than
> into the machine running the sensor.

The feeder is [`demo/adc_feeder.py`](../demo/adc_feeder.py) — stdlib
only, so it runs in the labmon image with nothing added, and it is not
part of the installed package.

## The demo channels

[`demo/calibration.demo.toml`](../demo/calibration.demo.toml) wires
channels to each conversion mode that works without scipy, which is what
lets the **Calibration layer** panel show a different kind of conversion
depending on the channel picked from the dropdown.

| Channel | Sensor | Mode | Reads as |
|---|---|---|---|
| `A0` | Diode on a cold finger | `piecewise_linear` | ~12–44 K |
| `A1` | Bipolar rail monitor | `affine` | ±3.2 V |
| `A2` | Pirani gauge | `expression` | 10⁻⁹–10⁻⁷ mbar |
| `A3` | Photodiode | `linear` | 82–108 mW |
| `A4`, `A5` | Quadrant photodiode | `affine` | ±30 µm |

`A4` and `A5` end up with the **same** `calibration_id`, because their
conversions genuinely are identical — the id describes the conversion,
not the channel.

`A2` is worth a look on the dashboard: its response is logarithmic in
pressure, so a few hundred millivolts of drift at the ADC sweeps two
decades on the log axis. `A1` uses a dimensionless conversion factor, so
its unit comes from the offset rather than the factor.

One simulated sensor doesn't fit the ADC story at all: `mock-wavemeter`
reports a frequency directly, as a real wavemeter does, with no voltage
in between. It feeds the detuning gauge, and it is stored in **hertz** so
Grafana's own SI scaling handles the units — a few MHz of drift on a
276 THz carrier is one part in 10⁸, so the gauge plots the offset from
276.5613 THz rather than the absolute frequency, which would never
visibly move.

Each channel also carries a `[provenance]` table. It never reaches
InfluxDB — it is logged at startup, which is where to look to see what
was in force during a run:

```bash
docker compose logs demo-serial-sensor | head
```

## The dashboard needs one panel plugin

The detuning gauge is a needle dial, which Grafana has no built-in panel
for. It comes from `briangann-gauge-panel`, and `.env.example` asks for it
by pinned version:

```
GRAFANA_PLUGINS=briangann-gauge-panel@2.2.0
```

Compose passes that straight through, and **only that** — the variable
defaults to empty, so a server deployment installs nothing and needs no
network when Grafana boots. The cost of leaving it unset is that one panel
renders as "plugin not found"; everything else works.

<details>
<summary><b>Three details in that one line that are easy to get wrong</b></summary>

<br>


- **The variable is `GF_PLUGINS_PREINSTALL_SYNC`, not
  `GF_INSTALL_PLUGINS`.** The obvious spelling is deprecated in this image
  and silently ignored unless `GF_INSTALL_PLUGINS_FORCE=true` is also set
  — it installs nothing and reports no error.
- **`_SYNC` rather than the async form**, so the install finishes before
  Grafana starts serving. Otherwise provisioning can load the dashboard
  while its panel type is still missing.
- **The version is pinned.** `preinstall_auto_update` defaults to true, so
  an unpinned id would silently follow upstream releases.

</details>

> [!NOTE]
> If the panel still reports "plugin not found" after the container is up,
> the tab is stale rather than the install broken. A hard reload
> (Ctrl+Shift+R) fixes it — see below.

The
available-panels map is baked into `index.html` at page load, so a tab
opened before the plugin registered never learns about it.

The plugin's own options schema misspells one key (`ticknessGaugeBasis`),
so the dashboard JSON has to spell it the same way; `pyproject.toml`
allows exactly that identifier and no other use of the misspelling.

## Grafana wrinkles worth knowing if you edit the dashboard

<details>
<summary><b>The Calibration layer panel's unit follows the dropdown</b></summary>

<br>

Its left axis is kelvin, mbar, volts, mW or µm depending on the channel,
and a panel's unit is otherwise a fixed setting. A second hidden query looks
up the Grafana unit id for the selected channel, and the `configFromData`
transformation applies it to the axis. Anything the lookup does not
recognise falls back to `suffix:<unit>`, so a new unit still renders
sensibly without touching the dashboard.

</details>

<details>
<summary><b>The XY panel needs an explicit <code>pluginVersion</code></b></summary>

<br>

Without one, Grafana
treats the panel as pre-11.1 and runs the xychart migration, which
expects `series[].x` to be a plain field name rather than a matcher. It
yields zero series and the panel renders "No data" with no error
anywhere. Any hand-written xychart panel needs `"pluginVersion"` set to
the Grafana version it targets.

Its two queries must not share field names either. Grafana
disambiguates duplicate field names across frames, which defeats the
`byName` matchers the manual series mapping uses. The current-position
query therefore selects `now-x`/`now-y` where the trace query selects
`beam-x`/`beam-y`.

</details>

<details>
<summary><b>Stat sparklines are linear-only, which is why the pressure tile has none</b></summary>

<br>

A stat panel registers no custom field config, so
`scaleDistribution` — the option that puts the Vacuum panel on a log axis
— is unavailable to it. `A2` spans two decades, and two decades on a
linear axis flatten against the axis while one maximum sets the top: a
correct number above a misleading graph. `graphMode: "none"` drops the
graph and leaves the trend to the Vacuum panel.

That tile's unit is `suffix:mbar` with `decimals` left unset, which is
what gets a unit onto a value like `9.91e-10` — see [units and scientific
notation](grafana.md#latest-value-panels) for why the obvious alternatives
do not.

</details>

<details>
<summary><b>The inventory table formats its readings in SQL, for the same reason</b></summary>

<br>

Its `value` column holds every measurement at once, from a 276 THz
carrier down to 10⁻⁹ mbar, and a column carries one `decimals` setting
for all of them: any fixed value renders the pressure rows as `0.0000`,
and auto decimals rounds the carrier to `277`. Each `UNION ALL` branch
therefore casts its own reading to text at its own precision, and the
panel does no numeric formatting at all. That is also what lets the
wavemeter row be shown in THz — where the trailing digits are the drift
the detuning gauge plots — while the database keeps storing hertz.
Because the column is text, it needs an explicit right-align; `auto`
aligns text left.

</details>

<details>
<summary><b>A table only has the columns something has written to it</b></summary>

<br>

InfluxDB 3 creates columns on write, so a table only has the columns
something has actually written to it. `frequency` is written solely by a
mock sensor, so it has no `calibration_id` or `input_volts` column at
all — and a `UNION ALL` across measurements fails to plan rather than
returning nulls.

The "What is feeding this dashboard" panel therefore casts explicit nulls
for that branch:

```sql
UNION ALL SELECT 'frequency', sensor_id, CAST(NULL AS VARCHAR) AS calibration_id, ...
```

Adding another measurement that only mock sensors write means adding the
same casts, or the panel breaks.

</details>

## Turning it off

The demo profile is opt-in; a server deployment simply doesn't set it.
See [`docs/deployment.md`](deployment.md#choosing-what-runs-compose_profiles).

To stop everything:

```bash
docker compose --profile demo down
```

To discard the demo's readings but keep the instance usable, drop the
database rather than the data directory:

```bash
docker compose exec influxdb influxdb3 delete database "$INFLUXDB_DATABASE" \
  --hard-delete now -y --token "$INFLUXDB3_AUTH_TOKEN"
```

The writers recreate the database and its tables on their next write, so
restarting the sensors is enough to start over.

> [!WARNING]
> Deleting `.influxdb3/data` also works, but it is a bigger hammer than it
> looks: `catalog/` lives in there alongside `dbs/`, and the catalog is
> where tokens are stored. Wiping the directory invalidates
> `INFLUXDB3_AUTH_TOKEN`, so every sensor and Grafana itself stop
> authenticating until a new token is created and `.env` updated.
