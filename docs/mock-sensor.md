# Mock sensor

Simulates a sensor so the rest of the stack — dashboards, alerting, a
client's network path — can be built and proved without hardware. Readings
come from a mean-reverting random walk around a setpoint.

It is not temperature-specific: `--measurement`, `--unit` and `--log-scale`
between them cover pressures, powers, voltages and frequencies. For reading
an actual board, see [`serial-sensor.md`](serial-sensor.md).

## Usage

```bash
# --measurement and --unit are required; the rest default to sensor-id
# "mock-sensor-1", setpoint 21, one reading every 5s
labmon mock-sensor --measurement temperature --unit "°C"

# A second room thermometer, sampled every second
labmon mock-sensor --sensor-id room-2 --measurement temperature \
  --setpoint 22 --interval 1 --unit "°C"

# A cryogenic sensor
labmon mock-sensor --sensor-id cryo-77k --measurement temperature \
  --setpoint 77 --noise 0.3 --unit K

# A vacuum gauge. Values spanning orders of magnitude need --log-scale,
# so noise and mean-reversion scale multiplicatively rather than by a
# fixed absolute amount.
labmon mock-sensor --sensor-id chamber-1 --measurement pressure \
  --setpoint 1e-7 --noise 0.05 --log-scale --unit mbar
```

Stop with Ctrl+C, or `docker stop` when run as a service — the process
closes its InfluxDB connection cleanly on SIGINT and SIGTERM.

## Options

| Flag | Default | Purpose |
|---|---|---|
| `--sensor-id` | `mock-sensor-1` | Tag identifying the sensor |
| `--measurement` | **required** | InfluxDB measurement (table) to write to |
| `--unit` | **required** | Unit of the reading (`°C`, `K`, `mbar`), written as a `unit` tag |
| `--interval` | `5.0` | Seconds between readings |
| `--setpoint` | `21.0` | Baseline the walk reverts toward |
| `--noise` | `0.1` | Std dev of Gaussian noise added each step |
| `--log-scale` | off | Walk in log10 space — see [below](#--log-scale) |
| `--resolution` | unset | Absolute step the reading is rounded to, in its own units |
| `--significant-digits` | `6` | Digits a reading carries when `--resolution` is not given |
| `--field` | `value` | InfluxDB field name for the reading |
| `--log-level` | `INFO` | `DEBUG` adds a line per reading |
| `--summary-interval` | `30.0` | Seconds between "still writing" lines; `0` turns them off |

Every setting in the project, not just this script's, is indexed in
[`configuration.md`](configuration.md).

### `--log-scale`

The default linear walk adds noise of a fixed absolute size. That is right
for room temperature and wrong for vacuum pressure: a stddev tuned for
`1e-7` is meaningless at `1e-3`, and a large enough absolute step could push
the reading below zero, which a pressure cannot be.

With `--log-scale` the walk operates on `log10(reading)`, so `--noise`
becomes a proportional quantity — jitter and mean-reversion scale with
magnitude, and the reading can never go non-positive. `--setpoint` stays in
ordinary linear units either way.

### `--resolution` and `--significant-digits`

A random walk in floating point produces `76.85006139177405` — sixteen
digits, claiming a precision no thermometer has. Somebody opening the
exported column cannot tell simulated jitter from a real millikelvin, so
readings are rounded before they are written.

`--resolution` is an absolute step in the reading's own units, and is how an
instrument with a fixed least-significant digit is described:

```bash
labmon mock-sensor --sensor-id cryo-77k --measurement temperature \
  --setpoint 77 --unit K \
  --resolution 0.001        # 76.85006139177405 -> 76.85
```

Without one, readings are rounded to `--significant-digits`. That is the
default because it stays meaningful wherever the sensor sits on the scale:
an absolute step large enough for a thermometer reports a vacuum gauge
walking at `1e-7 mbar` as exactly zero, whereas six significant digits
resolves it to `1e-12 mbar`.

> [!WARNING]
> The case needing care is a large value with fine structure. Six
> significant digits at a 276 THz carrier is a 1 GHz step, so a wavemeter
> drifting by a few MHz would return the same number every time — the demo
> gives it `--resolution 1e5`. As a rule, pick a step at least an order of
> magnitude below `--noise`.

The walk keeps full precision internally. Rounding its state would change
how it reverts toward the setpoint, and a step coarser than the noise would
freeze it outright; only the reported value is rounded.

<details>
<summary><b>Why <code>--measurement</code> and <code>--unit</code> are required</b></summary>

<br>

Neither can be guessed from the others. A walk around the default setpoint
of 21.0 is a plausible room in celsius and a plausible cryostat stage in
kelvin, and a reading that cannot say which is not worth recording — it is
the one thing an exported column cannot reconstruct later.

Defaulting them was worse than requiring them. A default `°C` would quietly
have labelled a `--measurement pressure` run in celsius, and a wrong unit is
more dangerous than a missing one: a missing unit sends somebody to check, a
wrong one does not.

An empty string is refused too. `--unit ""` is the same unlabelled reading
with an extra step.

</details>

<details>
<summary><b>Why the unit is a tag and not a field</b></summary>

<br>

Units are metadata you filter and group by — picking a unit-aware Grafana
display, or telling `K` from `°C` readings inside the same `temperature`
measurement — not a measured value. So they belong in InfluxDB's indexed tag
space rather than mixed in with the numeric field data. Left unset, no
`unit` tag is written at all.

</details>

## Where it writes

Read from the environment, and from `.env` when the command runs in the
directory holding it (see [`configuration.md`](configuration.md)):

| Variable | Default | Purpose |
|---|---|---|
| `INFLUXDB3_AUTH_TOKEN` | *(required)* | Auth token for the InfluxDB API |
| `INFLUXDB_HOST` | `http://localhost:8181` | InfluxDB server URL |
| `INFLUXDB_DATABASE` | `lab` | Target database |

> [!NOTE]
> `INFLUXDB_HOST` is deliberately absent from `.env.example`. The
> containerised mock sensors need the Docker network hostname
> (`http://influxdb:8181`, set in `docker-compose.yml`), while a host-side
> run needs `http://localhost:8181`. One `.env` value cannot satisfy both,
> so set it inline for an ad-hoc host-side run:
> `INFLUXDB_HOST=http://elsewhere:8181 labmon mock-sensor …`

## Checking what was written

```bash
labmon query --measurement temperature --since 5m
labmon query latest
```

See [`export.md`](export.md) for the full selection flags and the file
formats.
