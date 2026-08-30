# Configuration reference

Every setting labmon exposes, in one place. Behaviour is tuned in four
places — the environment, a command line, the calibration file, and a
per-user configuration file — and which one a given knob lives in is not
always guessable. This page is the index; the per-component docs explain
what each setting is *for*.

There is a fifth category, at the bottom: the constants that are
deliberately **not** configurable, and what breaks if you change them
anyway.

## Environment variables

Read from the process environment, and from `.env` when a command runs in
the directory holding that file. The process environment wins: a container,
a systemd unit and a `VAR=x labmon …` prefix all keep the value they set.

Only the working directory is read, never a parent, so a `.env` further up
the tree belonging to something else cannot configure a sensor. When the
file supplies a value the command says so, naming the file — worth reading
if two checkouts sit side by side, since it is the line that distinguishes
a run against the test stack from one against the real one.

`.env` is **not** read by your shell. That matters wherever a command runs
somewhere else: a sensor under systemd on a client machine gets its
settings from the unit, not from a file in a checkout. To use `.env` from
another directory, either export it —

```bash
set -a; . ./.env; set +a
```

— or install [direnv](https://direnv.net/), which does it on `cd`. An
`.envrc` containing `dotenv` is committed, so `direnv allow` is the whole
setup.

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
| *(not a variable)* `--log-filter` | see `docker-compose.yml` | InfluxDB's log level per module. Lowers the once-a-second WAL flush line, which is otherwise its entire output — see docs/logging.md |
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
| `--resolution` | *(none)* | Absolute step readings are rounded to, in their own units |
| `--significant-digits` | `6` | Digits a reading carries when `--resolution` is unset |
| `--log-level` | `INFO` | `DEBUG` adds a line per reading |
| `--summary-interval` | `30.0` | Seconds between "still writing" lines; `0` turns them off |

### `labmon export` and `labmon query`

Both take the same selection flags. See
[`docs/export.md`](export.md) for the formats and the window spellings.

| Flag | Default | Purpose |
|---|---|---|
| `--measurement` | *(all)* | Measurement to read; repeatable |
| `--sensor-id` | *(all)* | Restrict to one sensor; repeatable |
| `--since` | `1h` | Window start: ISO 8601 timestamp or a duration ago |
| `--until` | *(now)* | Window end, same spellings |
| `--format` | *(from `-o`, else `csv`)* | `csv`, `parquet`, `feather`, `netcdf` — export only |
| `-o`, `--output` | `labmon-export.<ext>` | File to write, or `-` for stdout — export only |
| `--split-per-sensor` | off | One file per sensor — export only |
| `--no-raw-input` | off | Drop `input_volts` and `calibration_id` — export only |
| `--limit` | `20` | Rows printed; `0` shows every one — query only |

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

They do decide how finely `value` is rounded — see
[How many digits a reading keeps](#how-many-digits-a-reading-keeps) —
and digits dropped on the way in do not come back. `input_volts` keeps
all of its own, which is what makes the correction possible.

## The user configuration file

Everything above configures a *deployment* — a host, a database, a
token — and reaches labmon through the environment, because that is how
a container is configured. The settings in this section configure a
*person*: how readings are shown to whoever is reading them. They live
with that person's other dotfiles rather than with the stack.

Read from `$XDG_CONFIG_HOME/labmon/labmon.toml`, or
`~/.config/labmon/labmon.toml` when `XDG_CONFIG_HOME` is unset. **Not
having one is the ordinary case**: every key has a default and a missing
file is not an error.

```toml
timezone = "Europe/Paris"
```

| Key | Default | Purpose |
|---|---|---|
| `timezone` | `"UTC"` | Zone the `time` column is printed in. An IANA name, or `"local"` for the machine's own zone |
| `monitor.refresh` | `"2s"` | How often `labmon monitor` redraws |
| `monitor.window` | `"15m"` | How much history the panel's statistics cover |
| `monitor.theme` | `"nord"` | Colours the panel opens in — any of Textual's twenty-one themes |
| `[[monitor.panels]]` | *(none)* | A tile per entry. With none, the panel shows a table of every sensor |
| `[[monitor.sensors]]` | *(none)* | How many digits one sensor is worth, in the table and in any tile |

Each `[[monitor.panels]]` entry takes:

| Key | Default | Purpose |
|---|---|---|
| `sensor_id` | *(required)* | Which sensor the tile shows |
| `measurement` | *(the sensor's only one)* | Which table, for a sensor that writes to more than one |
| `title` | *(the sensor id)* | What to write above the number |
| `precision` | *(the sensor's rule, else as stored)* | Decimal places, trailing zeros included |
| `format` | `"auto"` | `auto`, `plain` or `scientific` |
| `warn_above`, `warn_below` | *(none)* | Colour the tile when the reading leaves this range |

Each `[[monitor.sensors]]` entry takes a subset — a display rule has no
title and no threshold, because it governs sensors that have no tile:

| Key | Default | Purpose |
|---|---|---|
| `sensor_id` | *(required)* | Which sensor the rule governs |
| `measurement` | *(all of them)* | Which table, for a sensor that writes to more than one. A rule naming it wins over one that does not |
| `precision` | *(as stored)* | Decimal places, trailing zeros included |
| `format` | `"auto"` | `auto`, `plain` or `scientific` |

A tile that names its own `precision` or `format` overrides the sensor's
rule for that tile. Readings are otherwise shown exactly as stored:
only the average and the deviation are rounded, and only against each
other. These rules live under `[monitor]`, so `labmon query latest` is
unaffected.

```toml
timezone = "Europe/Paris"

[monitor]
refresh = "2s"
window  = "15m"
theme   = "nord"

[[monitor.sensors]]
sensor_id = "beam-x"
precision = 2

[[monitor.panels]]
sensor_id = "cryo-77k"
title = "Cold finger"
precision = 3
warn_above = 80.0
```

A layout can also live in a file of its own, passed with
`labmon monitor --config bakeout.toml`. That file has the same shape
minus the `monitor.` prefix, and **replaces** this section rather than
merging with it — see [`monitor.example.toml`](../monitor.example.toml)
and [`docs/monitor.md`](monitor.md).

Durations are spelled the way `--since` spells them — `2s`, `90m`,
`24h`, `7d`, `1w` — because one parser serves both. A bare number is
refused: `2` could be seconds or minutes, and a panel that guesses wrong
refreshes sixty times too often. Both `[monitor]` values are validated
when the file is read rather than when they are first used, since a
mistake that surfaced on the first tick would already have taken over
the terminal. See [`docs/monitor.md`](monitor.md).

`theme` is checked the same way, against the list the panel itself
offers — `gruvbox`, `dracula`, `solarized-light`, `tokyo-night` and the
rest. A name it does not have is refused before the panel starts, with
the list in the message, rather than raising over a screen it has
already taken.

`timezone` changes only what a terminal prints. Readings are stored in
UTC and exported in UTC — a data file that carried somebody's desk clock
would be unusable to the next person to open it. What it fixes is the
question you actually ask standing next to an experiment: whether the
cryostat was cold at 3 a.m. *your* time.

`"local"` is resolved by asking the machine, so a laptop that travels
follows it. A name that looks right but is rejected usually means the
system timezone database is not installed — `tzdata` provides it.

**An unrecognised key is an error, not a warning.** A config file that
skips what it does not understand gives a misspelled setting the exact
appearance of a working one, with nothing to notice. The cost is that a
key added by a newer labmon fails on an older one, which is a message
naming the key rather than a silent wrong answer.

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
| `offset_resolution` | *(exact)* | `affine` |
| `significant_digits` | *(derived)* | Every channel |
| `voltages`, `values`, `value_unit` | *(required)* | `spline`, `piecewise_linear` |
| `expression`, `value_unit` | *(required)* | `expression` |

### How many digits a reading keeps

A converted value is rounded to the resolution its input actually had,
so a stored reading does not claim seventeen digits of a twelve-bit
measurement. The step comes from `--resolution-bits` and `--vref`,
carried through the conversion: a 12-bit input over 3.3 V steps by
806 µV, and a factor of `42.5 kelvin / volt` turns that into 34 mK, so
that channel is written to hundredths of a kelvin.

Only `linear` and `affine` have one step across their whole range. A
spline's slope varies along its curve and an expression's is unknown to
the loader, so those channels keep the computed value unless the file
names `significant_digits`.

`significant_digits` overrides the derived step wherever it is set.
Significant rather than decimal places, because a calibrated channel may
sit anywhere on the scale — three decimals is far too coarse for a gauge
reading 1e-8 mbar and far too fine for a room thermometer.

`offset_resolution` says how well an affine offset is itself known, for
one that came out of a fit rather than a divider ratio. It is
independent of the ADC's own step, so the two combine in quadrature. An
offset otherwise shifts the scale without dividing it, and adds nothing.
The key is rejected on the other modes rather than ignored, since none
of them has an offset.

Rounding happens once, on the way in, and reaches `value` only.
`input_volts` keeps every digit it had: it is what a corrected
calibration would be re-applied to, and rounding it would make that
irrecoverable. The `calibration_id` tag does not change either — it says
which conversion produced a reading, and how many digits survived is as
much a property of the board, which is not recorded.

A board that averages several conversions per reading resolves finer
than one ADC step and says so by reporting a fractional count. The host
cannot see how many were averaged, so the derived step stays
conservative; a channel that wants those digits states them.

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

Once it is full, the oldest point is dropped to make room for the newest
and acquisition continues — a monitoring system is better served by
current data than by a stalled sampling loop. Drops are counted, warned
about once, and reported per summary window, so shedding load is never
silent.

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
| `_CHANNEL_PATTERN` | Widening it lets arbitrary text become a permanent InfluxDB tag; narrowing it stops a board's existing channels parsing |
| `MAX_WARNED_CHANNELS` | Only how many uncalibrated channels are named in the log before the warnings stop naming them |

Everything else with a `DEFAULT_` prefix is the default of a parameter
next to it, and can be changed by passing a value — which survives an
upgrade, and is visible in the deployment. Editing installed source does
neither.
