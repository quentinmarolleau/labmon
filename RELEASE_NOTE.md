# labmon v0.2.0-beta.1

labmon records what an experiment produces — cryostat temperature, chamber
pressure, laser power, a magnet's current — into a time-series database, and
puts it on a live dashboard anyone in the room can open in a browser. It is
built from [InfluxDB 3](https://docs.influxdata.com/influxdb3/core/) and
[Grafana](https://grafana.com/docs/grafana/latest/), wired together and
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
- **Calibration in a small text file**, with five conversion modes — linear,
  affine, spline, piecewise-linear, and arbitrary expressions. Recalibrating
  never means reflashing a board.
- **Units checked by dimensional analysis.** Declare a channel as
  `42.5 kelvin / volt` and the result is derived in kelvin. Adding millibars
  to kelvin fails when the file loads, not after a week of recording.
- **Every conversion trial-applied at load time**, so a typo or a dimensional
  mistake fails immediately with a clear message rather than silently
  recording wrong numbers.
- **Recording stops while an instrument is off**, rather than filling the
  database with a flat line that means nothing.

### Not losing data

- **A queue-backed writer** between the sampling loop and the database. A
  single InfluxDB write costs about a second, because the database waits until
  the data is on disk; batching turns that into a millisecond per point, and
  the queue means the sampling loop never waits on the network at all.
- **Outages are ridden out.** Writes are retried with backoff while sensors
  keep sampling, and the backlog drains afterwards.
- **Load is shed, not blocked.** If an outage outlasts the queue, the oldest
  point is dropped so acquisition continues — and the drops are counted,
  warned about once, and reported every summary window, so it is never silent.
- **A channel that goes quiet is distinguishable from one reading zero.**
  Every sensor appears in the periodic summary even when it wrote nothing,
  which is the case most worth seeing: the trace is flat either way.

### Seeing it

- **Two provisioned dashboards** — Lab Overview and Logs — restored
  automatically on a fresh install rather than living in one browser's memory.
- **Grafana itself is the front end**, so alerting, Explore, annotations,
  snapshots and variables are available immediately.
- **Dashboards built in the browser can be committed** to
  `grafana/dashboards/` and come back on every install.

### Logs

- **Log aggregation behind a profile.** Loki stores lines; Alloy collects them
  from every container and from systemd units on the host.
- **Lines are labelled by the reading they describe**, not the container that
  emitted them, so `{sensor_id="cryo-77k"}` finds a sensor's logs wherever it
  runs — including one process reporting several channels.
- **Client machines ship to the same store**, over an authenticated endpoint,
  so one query answers "why did this stop" for the whole lab.
- **Structured, severity-tagged output** in logfmt, queryable by field.

### Running it across a lab

- **Server, clients and viewers.** Database and dashboards on one always-on
  machine; sensor scripts wherever the instrument is wired; a browser for
  everyone else.
- **Clients run as containers or as a plain Python install**, both documented,
  with systemd units for machines that cannot run containers.
- **TLS behind a profile** for networks where the trusted-LAN assumption does
  not hold: a reverse proxy with its own CA in front of InfluxDB, Grafana and
  Loki's push endpoint, without disturbing clients that have not moved yet.

## Security posture

Plaintext ports bind to loopback by default, so a fresh install publishes
nothing to the network around it. The LAN-facing path is the `tls` profile,
which is encrypted and authenticated throughout — and with it active the
plaintext ports stay closed, so the proxy is the only way in.

Loki is never published. Its push endpoint is reachable only through the
proxy, behind a credential, and only for writing: the same host and port
answers 404 to every read.

[`SECURITY.md`](SECURITY.md) carries the private disclosure route and the
three assumptions the design rests on.

## Upgrading

Nothing to upgrade *from* — this is the first tagged release. Two notes for
anyone already running the stack from `main`:

**InfluxDB and Grafana now bind to `127.0.0.1`.** A machine that other
computers connect to must set `LABMON_BIND_ADDRESS=0.0.0.0` in `.env`, and
should set a real `GRAFANA_ADMIN_PASSWORD` at the same time — opening the port
is when the default stops being a local convenience. TLS deployments are
unaffected.

**Quote the Loki push hash in `.env`.** Compose expands `$name` in an unquoted
value, and a bcrypt hash is full of `$`, so an unquoted hash arrives truncated
and every push is refused with no indication why:

```dotenv
LABMON_LOKI_PUSH_HASH='$2a$14$...'
```

Single quotes suppress the expansion; double quotes do not.

## Known limits

- **The serial path has never met a physical board.** It is tested end to end
  against a virtual serial port and against a feeder speaking the firmware's
  wire format, but the last centimetre is unproven. Check readings against a
  known voltage before trusting them.
- **Traffic is plaintext unless the `tls` profile is on.** Deliberate — it
  assumes a trusted lab network — and it is a setting, not a rewrite.
- **No export command yet.** Getting data out means SQL or Grafana's per-panel
  CSV export, neither of which suits pulling a run into a notebook. A
  `labmon-export` CLI is the next release's headline.
- **A long outage is recorded as its final stretch.** The queue holds a fixed
  number of points, so what survives is the most recent window rather than the
  whole gap.

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
