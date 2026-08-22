# Configuration reference

Every setting labmon exposes, in one place. Behaviour is tuned in three
places — the environment, a command line, and the calibration file — and
which one a given knob lives in is not always guessable. This page is the
index; the per-component docs explain what each setting is *for*.

There is a fourth category, at the bottom: the constants that are
deliberately **not** configurable, and what breaks if you change them
anyway.

## Environment variables

Read from `.env` by Compose, or from the process environment for a bare
install. `.env` is **not** read by your shell — a value set there reaches
a container but not a `uv run mock-sensor` you type yourself, unless
something like direnv exports it.

### Connection settings

Every sensor needs these, wherever it runs.

| Variable | Default | Purpose |
|---|---|---|
| `INFLUXDB3_AUTH_TOKEN` | *(required)* | InfluxDB API token. No default, and startup fails without it |
| `INFLUXDB_HOST` | `http://localhost:8181` | Where to write. Containers on the server use `http://influxdb:8181`; a client machine uses the server's address |
| `INFLUXDB_DATABASE` | `lab` | Database readings are written to |
| `INFLUXDB_TLS_CA` | *(unset)* | Path to the server's CA certificate, when it runs behind the `tls` profile's proxy. Unset, the client behaves exactly as before |

`INFLUXDB_TLS_CA` is needed only when `INFLUXDB_HOST` is an `https://`
address served by the stack's own CA, since a private root is in no
system trust store. One variable covers both directions of traffic — the
same file is read by the write client and by the query client. A path
that does not exist fails at startup rather than at the first write,
because the TLS layer would otherwise report it as a verification failure
against the *server*, which reads as a certificate problem rather than as
a typo in this machine's env file. See
[`docs/client-setup.md`](client-setup.md#connecting-over-tls) for the
client side and
[`docs/deployment.md`](deployment.md#encrypting-client-and-viewer-traffic)
for the server side.

`INFLUXDB_HOST` is set per service in the Compose files rather than in
`.env`, because one value cannot serve both a container (which needs the
Docker network name) and a host-side process (which needs `localhost`).

**Set-but-empty is not the same as unset.** Compose substitutes its own
default for an empty value, but Python does not: `INFLUXDB_DATABASE=`
with nothing after it makes a host-side sensor write to a database named
`""` rather than to `lab`. `.env.example` ships several keys in exactly
that shape, so either fill them in or comment them out.

### Server stack

Only meaningful on the machine running `docker-compose.yml`.

| Variable | Default | Purpose |
|---|---|---|
| `INFLUXDB_NODE_ID` | *(required)* | Node identifier for the InfluxDB instance |
| `GRAFANA_ADMIN_PASSWORD` | `admin` | Grafana admin login |
| `LABMON_BIND_ADDRESS` | `127.0.0.1` | Which address InfluxDB's and Grafana's ports bind to. Loopback by default; a server clients push to sets `0.0.0.0`. Does not affect the `tls` profile's proxy |
| `COMPOSE_PROFILES` | *(unset)* | Which optional services start — see below |
| `GRAFANA_PLUGINS` | *(unset)* | Panel plugins to preinstall, as `id@version`, comma-separated. Unset means Grafana needs no network at boot |
| `LOKI_RETENTION_PERIOD` | `720h` | How long a log line is kept, with the `logs` profile active. Never below `24h` — Loki accepts less and cannot honour it |
| `LABMON_TLS_INFLUXDB_SITES` | `https://127.0.0.1:8443` | Addresses the proxy answers on for InfluxDB, with the `tls` profile active. Comma-*and-space* separated; each entry a whole `https://host:port` |
| `LABMON_TLS_GRAFANA_SITES` | `https://127.0.0.1:3443` | The same for Grafana |
| `LABMON_TLS_DEFAULT_SNI` | `127.0.0.1` | Which certificate to serve when a client sends no server name, which is what dialling a bare IP does |

`COMPOSE_PROFILES` takes a comma-separated list:

| Value | Adds |
|---|---|
| *(unset)* | Nothing — just InfluxDB and Grafana, which is the real-server shape |
| `demo` | The mock sensors and the simulated board, for a stack with no hardware |
| `logs` | Loki and Alloy, collecting every container's output |
| `tls` | A Caddy reverse proxy terminating TLS on 8443 and 3443 — see [`docs/deployment.md`](deployment.md#encrypting-client-and-viewer-traffic) |

The two are independent, so `demo,logs` is valid. A profile is needed to
*stop* its services as well as start them: `docker compose down` without
it leaves them running. See
[`docs/deployment.md`](deployment.md#demo-vs-server-compose_profiles).

`ADC_FEEDER_HOST` also exists, but only the demo's simulated board reads
it — see [`docs/demo-stack.md`](demo-stack.md).

## Command-line flags

### `mock-sensor`

Simulates readings, so most of these shape a random walk rather than
describing hardware. Full description in
[`docs/mock-sensor.md`](mock-sensor.md).

| Flag | Default | Purpose |
|---|---|---|
| `--sensor-id` | `mock-sensor-1` | Tag identifying the sensor |
| `--measurement` | `temperature` | InfluxDB measurement (table) to write to |
| `--field` | `value` | Field name for the reading |
| `--unit` | *(none)* | Unit tag; omitted entirely when unset |
| `--interval` | `5.0` | Seconds between readings |
| `--setpoint` | `21.0` | Baseline the walk reverts toward |
| `--noise` | `0.1` | Standard deviation of the step noise |
| `--log-scale` | off | Walk in log₁₀ space, for a quantity spanning decades |
| `--log-level` | `INFO` | `DEBUG` adds a line per reading |
| `--summary-interval` | `30.0` | Seconds between "still writing" lines; `0` turns them off |

### `serial-sensor`

Reads a real board. Full description in
[`docs/serial-sensor.md`](serial-sensor.md).

| Flag | Default | Purpose |
|---|---|---|
| `--port` | *(required)* | Device path, or any pyserial URL (`rfc2217://`, `socket://`) |
| `--calibration` | *(required)* | Path to the TOML calibration file |
| `--baudrate` | `115200` | Ignored by a board on native USB, but pyserial requires a value |
| `--resolution-bits` | `12` | ADC resolution |
| `--vref` | `3.3` | ADC reference voltage |
| `--log-level` | `INFO` | `DEBUG` adds a line per reading |
| `--summary-interval` | `30.0` | Seconds between "still writing" lines; `0` turns them off |

`--vref` and `--resolution-bits` are *not* stored with a reading. They
don't need to be: both scale the recorded voltage linearly, so getting
either wrong stays correctable with a query.

## The calibration file

One `[channels.<name>]` section per channel the board streams.
[`calibration.example.toml`](../calibration.example.toml) works through
every mode; [`docs/serial-sensor.md`](serial-sensor.md#the-calibration-file)
explains the choices between them.

| Key | Default | Applies to |
|---|---|---|
| `sensor_id` | *(required)* | Every channel |
| `measurement` | *(required)* | Every channel |
| `mode` | `linear` | Every channel |
| `store_input` | `true` | Every channel; also settable file-wide at top level |
| `conversion_factor` | *(required)* | `linear`, `affine` |
| `offset` | *(required)* | `affine` |
| `voltages`, `values`, `value_unit` | *(required)* | `spline`, `piecewise_linear` |
| `expression`, `value_unit` | *(required)* | `expression` |

Two optional sub-tables per channel:

| Table | Purpose |
|---|---|
| `[channels.<name>.provenance]` | Free-form notes on how the calibration was obtained. Logged at startup, never written to InfluxDB. Any keys accepted |
| `[channels.<name>.stop_recording_when]` | Stops recording while the instrument is off — see below |

### `stop_recording_when`

| Key | Default | Purpose |
|---|---|---|
| `below` | *(none)* | Stop recording under this level |
| `above` | *(none)* | Stop recording over this level |
| `for` | `0s` | How long a breach must persist before recording stops |
| `resume_above` | `below` | Resume only once back over this — the deadband on `below` |
| `resume_below` | `above` | Resume only once back under this — the deadband on `above` |
| `raw_voltage` | `false` | Gate on the measured voltage rather than the converted value |

At least one of `below` or `above` is required, and the bounds must nest
as `below <= resume_above < resume_below <= above`. A file breaking that
is rejected at startup, as is a `resume_*` without the bound it
deadbands. Note that `for = "1m"` is one *metre*: minutes are `min`.

Full semantics, including why stopping is delayed and resuming is not, in
[`docs/serial-sensor.md`](serial-sensor.md).

## Library parameters

For a sensor written against `labmon.sensors.polling` — see
[`docs/custom-sensor.md`](custom-sensor.md). These have no CLI flag
because the caller *is* the program.

`poll()`:

| Parameter | Default | Purpose |
|---|---|---|
| `interval` | `5.0` | Seconds between reads |
| `initial_backoff` | `1.0` | First wait after a read raises |
| `max_backoff` | `60.0` | Ceiling the backoff doubles up to |
| `summary_interval` | `30.0` | Seconds between summary lines; `None` disables |

An instrument with a known recovery time — a controller that takes a
minute to reboot — should be given it rather than left to the defaults.

`PointWriter()`, which decouples writing from reading so a slow InfluxDB
never stalls a read loop:

| Parameter | Default | Purpose |
|---|---|---|
| `maxsize` | `10_000` | Queued points before `write()` starts blocking |
| `poll_interval` | `0.5` | Idle wake-up; bounds how long `close()` takes |
| `initial_backoff` | `1.0` | First wait after a failed batch |
| `max_backoff` | `30.0` | Ceiling the backoff doubles up to |

The queue depth is what decides how long an outage a sensor rides out:
ten thousand points is about a hundred seconds at 100 Hz, or most of a
day at one reading every five seconds. [`docs/latency.md`](latency.md)
has the measurements.

A sensor built on `SensorLoop` takes a writer rather than forwarding each
of these:

```python
from labmon.influx import get_client
from labmon.sensors.loop import SensorLoop
from labmon.writer import PointWriter

loop = SensorLoop(writer=PointWriter(get_client(), maxsize=100_000))
```

## What is deliberately not configurable

These are schema and protocol invariants. They are marked `INVARIANT` in
the source, so grepping for that finds them all.

| Constant | Consequence of changing it |
|---|---|
| `FIELD_NAME`, `INPUT_FIELD_NAME`, `CALIBRATION_ID_TAG` | Splits the history in two: rows already stored keep the old name, and no query spans both |
| `CALIBRATION_ID_LENGTH`, `FINGERPRINT_SIGNIFICANT_DIGITS` | Re-tags every calibration, so a new id never matches one in the database |
| `VOLTAGE_SYMBOL` | Invalidates every `expression` conversion in every calibration file at once |
| `_FIELD_SEPARATOR`, `_FIELDS_PER_LINE` | Breaks the wire contract: readings stop parsing until the board is reflashed to match |

Everything else with a `DEFAULT_` prefix is the default of a parameter
next to it, and can be changed by passing a value — which survives an
upgrade, and is visible in the deployment. Editing installed source does
neither.
