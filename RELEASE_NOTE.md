# labmon v0.2.0-beta.1

labmon records any quantity of interest — e.g. cryostat temperature,
chamber pressure, laser power, a magnet's current etc. — into a time-series
database, and puts it on a live dashboard anyone in the room can open in a
browser. It is built from [InfluxDB 3](https://docs.influxdata.com/influxdb3/core/)
and [Grafana](https://grafana.com/docs/grafana/latest/), wired together and
pre-configured so neither has to be set up by hand.

This is the first beta: the feature set is settled, the interfaces may still
move, and everything below runs today.

## Trying it

Two commands and a browser tab, no hardware:

```bash
cp .env.example .env
docker compose up -d --wait
```

Nine channels start writing immediately. Five are simulated. The other four
run the *real* acquisition path — raw ADC counts arriving in the reference
firmware's wire format, converted exactly as they would be from a board on a
serial port. Only the board itself is simulated, so the demo exercises the
code that matters rather than a mock of it.

## What is in the beta

### Recording

- **Three ways in.** A board over serial (raw ADC counts, converted on the
  computer); an instrument with its own SDK, REST endpoint or CLI, through a
  copyable template; and simulated sensors for trying things out.
  [`docs/serial-sensor.md`](docs/serial-sensor.md) ·
  [`docs/custom-sensor.md`](docs/custom-sensor.md) ·
  [`docs/mock-sensor.md`](docs/mock-sensor.md) —
  [#23](https://github.com/quentinmarolleau/labmon/pull/23), [#38](https://github.com/quentinmarolleau/labmon/pull/38), [#11](https://github.com/quentinmarolleau/labmon/pull/11)
- **Calibration in a small text file**, with five conversion modes — linear,
  affine, spline, piecewise-linear, and arbitrary expressions.
  [`docs/serial-sensor.md`](docs/serial-sensor.md) ·
  [`calibration.example.toml`](calibration.example.toml) — [#21](https://github.com/quentinmarolleau/labmon/pull/21)
- **Units checked by dimensional analysis.** Declare a channel as
  `42.5 kelvin / volt` and the result is derived in kelvin. Adding millibars
  to kelvin fails when the file loads, not after a week of recording.
  [`docs/serial-sensor.md`](docs/serial-sensor.md) — [#21](https://github.com/quentinmarolleau/labmon/pull/21)
- **Every conversion trial-applied at load time**, so a typo or a dimensional
  mistake fails immediately with a clear message rather than silently
  recording wrong numbers.
  [`docs/serial-sensor.md`](docs/serial-sensor.md) — [#21](https://github.com/quentinmarolleau/labmon/pull/21), [#69](https://github.com/quentinmarolleau/labmon/pull/69)
- **Recording stops while an instrument is off**, rather than filling the
  database with a flat line that means nothing.
  [`docs/serial-sensor.md`](docs/serial-sensor.md) ·
  [`docs/configuration.md`](docs/configuration.md) — [#76](https://github.com/quentinmarolleau/labmon/pull/76)

### Not losing data

- **A queue-backed writer** between the sampling loop and the database. A
  single InfluxDB write costs about a second, because the database waits until
  the data is on disk; batching turns that into a millisecond per point, and
  the queue means the sampling loop never waits on the network at all.
  [`docs/latency.md`](docs/latency.md#why-the-influxdb-write-costs-a-second)
- **Outages are ridden out.** Writes are retried with backoff while sensors
  keep sampling, and the backlog drains afterwards.
  [`docs/latency.md`](docs/latency.md) — [#16](https://github.com/quentinmarolleau/labmon/pull/16)
- **Load is shed, not blocked.** If an outage outlasts the queue, the oldest
  point is dropped so acquisition continues — and the drops are counted,
  warned about once, and reported every summary window, so it is never silent.
  [`docs/latency.md`](docs/latency.md) ·
  [`docs/configuration.md`](docs/configuration.md) — [#142](https://github.com/quentinmarolleau/labmon/pull/142)
- **A channel that goes quiet is distinguishable from one reading zero.**
  Every sensor appears in the periodic summary even when it wrote nothing,
  which is the case most worth seeing: the trace is flat either way.
  [`docs/logging.md`](docs/logging.md) — [#121](https://github.com/quentinmarolleau/labmon/pull/121), [#99](https://github.com/quentinmarolleau/labmon/pull/99)

### Seeing it

- **Two provisioned dashboards** — Lab Overview and Logs — restored
  automatically on a fresh install rather than living in one browser's memory.
  [`docs/grafana.md`](docs/grafana.md) — [#13](https://github.com/quentinmarolleau/labmon/pull/13), [#30](https://github.com/quentinmarolleau/labmon/pull/30), [#72](https://github.com/quentinmarolleau/labmon/pull/72)
- **Grafana itself is the front end**, so alerting, Explore, annotations,
  snapshots and variables are available immediately.
  [`docs/grafana.md`](docs/grafana.md)
- **Dashboards built in the browser can be committed** to
  `grafana/dashboards/` and come back on every install.
  [`docs/grafana.md`](docs/grafana.md) — [#6](https://github.com/quentinmarolleau/labmon/pull/6)

### Logs

- **Log aggregation behind a profile.** Loki stores lines; Alloy collects them
  from every container and from systemd units on the host.
  [`docs/logging.md`](docs/logging.md) — [#39](https://github.com/quentinmarolleau/labmon/pull/39), [#65](https://github.com/quentinmarolleau/labmon/pull/65)
- **Lines are labelled by the reading they describe**, not the container that
  emitted them, so `{sensor_id="cryo-77k"}` finds a sensor's logs wherever it
  runs — including one process reporting several channels.
  [`docs/logging.md`](docs/logging.md#the-sensor_id-label) — [#133](https://github.com/quentinmarolleau/labmon/pull/133)
- **Client machines ship to the same store**, over an authenticated endpoint,
  so one query answers "why did this stop" for the whole lab.
  [`docs/logging.md`](docs/logging.md#logs-from-other-machines) ·
  [`docs/client-setup.md`](docs/client-setup.md) — [#134](https://github.com/quentinmarolleau/labmon/pull/134)
- **Structured, severity-tagged output** in logfmt, queryable by field.
  [`docs/logging.md`](docs/logging.md#what-a-sensor-line-looks-like) —
  [#75](https://github.com/quentinmarolleau/labmon/pull/75), [#132](https://github.com/quentinmarolleau/labmon/pull/132)

### Running it across a lab

- **Server, clients and viewers.** Database and dashboards on one always-on
  machine; sensor scripts wherever the instrument is wired; a browser for
  everyone else.
  [`docs/deployment.md`](docs/deployment.md) ·
  [`docs/client-setup.md`](docs/client-setup.md) — [#17](https://github.com/quentinmarolleau/labmon/pull/17), [#18](https://github.com/quentinmarolleau/labmon/pull/18)
- **Clients run as containers or as a plain Python install**, both documented,
  with systemd units for machines that cannot run containers.
  [`docs/client-setup.md`](docs/client-setup.md) · [`deploy/`](deploy) —
  [#18](https://github.com/quentinmarolleau/labmon/pull/18), [#65](https://github.com/quentinmarolleau/labmon/pull/65)
- **TLS behind a profile** for networks where the trusted-LAN assumption does
  not hold: a reverse proxy with its own CA in front of InfluxDB, Grafana and
  Loki's push endpoint, without disturbing clients that have not moved yet.
  [`docs/deployment.md`](docs/deployment.md#encrypting-client-and-viewer-traffic)
  — [#73](https://github.com/quentinmarolleau/labmon/pull/73), [#134](https://github.com/quentinmarolleau/labmon/pull/134)

## Security posture

Plaintext ports bind to loopback by default, so a fresh install publishes
nothing to the network around it. The LAN-facing path is the `tls` profile,
which is encrypted and authenticated throughout — and with it active the
plaintext ports stay closed, so the proxy is the only way in.
[`docs/deployment.md`](docs/deployment.md#exposing-the-server-to-the-lan) —
[#138](https://github.com/quentinmarolleau/labmon/pull/138), [#73](https://github.com/quentinmarolleau/labmon/pull/73)

Loki is never published. Its push endpoint is reachable only through the
proxy, behind a credential, and only for writing: the same host and port
answers 404 to every read.
[`docs/logging.md`](docs/logging.md#logs-from-other-machines) — [#134](https://github.com/quentinmarolleau/labmon/pull/134)

[`SECURITY.md`](SECURITY.md) carries the private disclosure route and the
three assumptions the design rests on, written down in
[`docs/deployment.md`](docs/deployment.md) — [#124](https://github.com/quentinmarolleau/labmon/pull/124), [#129](https://github.com/quentinmarolleau/labmon/pull/129)

## Known limits

- **The serial path has never met a physical board.** It is tested end to end
  against a virtual serial port and against a feeder speaking the firmware's
  wire format, but the last centimetre is unproven. Check readings against a
  known voltage before trusting them.
  [`docs/serial-sensor.md`](docs/serial-sensor.md) ·
  [`docs/demo-stack.md`](docs/demo-stack.md)
- **Traffic is plaintext unless the `tls` profile is on.** Deliberate — it
  assumes a trusted lab network — and it is a setting, not a rewrite.
  [`docs/deployment.md`](docs/deployment.md#encrypting-client-and-viewer-traffic)
- **No export command yet.** Getting data out means SQL or Grafana's per-panel
  CSV export, neither of which suits pulling a run into a notebook. A
  `labmon-export` CLI is the next release's headline — [#58](https://github.com/quentinmarolleau/labmon/issues/58)
- **A long outage is recorded as its final stretch.** The queue holds a fixed
  number of points, so what survives is the most recent window rather than the
  whole gap. Keeping the entire window at reduced resolution is tracked in
  [#141](https://github.com/quentinmarolleau/labmon/issues/141).

## What 1.0 means

Verified against real hardware, and running in at least two different labs for
at least six months.

Deliberately slow. Monitoring software is trusted by default once installed —
nobody re-derives whether the number on the dashboard is right — and the
failure mode of getting it wrong is a plausible-looking number that is quietly
incorrect. The version number should stay honest about how much has been
proven outside one bench.

## Quality bar

100% test coverage, basedpyright on its strictest preset with zero errors and
zero warnings, ruff, and a cold-start job that builds the whole stack from
nothing on every push and asserts that data arrives, that both dashboards'
queries execute, and that the TLS and log paths behave — including the
negative cases, so a proxy serving no TLS at all would fail rather than pass.

[`CONTRIBUTING.md`](CONTRIBUTING.md) has the reasoning and the workflow —
[#33](https://github.com/quentinmarolleau/labmon/pull/33), [#71](https://github.com/quentinmarolleau/labmon/pull/71), [#32](https://github.com/quentinmarolleau/labmon/pull/32), [#101](https://github.com/quentinmarolleau/labmon/pull/101)
