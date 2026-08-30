# Logs

Measurements say a reading stopped arriving. They never say why. The
reasons live in a container's output — an expired API token, an instrument
reporting busy, a traceback from a vendor SDK — and by default that is only
reachable by running `docker compose logs` on the right machine, with no
history beyond what Docker happens to still hold.

The `logs` profile adds two containers that fix this: **Loki** stores log
lines, and **Alloy** collects them. Both are Grafana's own, so the result is
queryable in the same Grafana that shows the measurements.

```
   every container's stdout ─┐
                             ├─► Alloy ──► Loki ──► Grafana
   systemd journal (labmon-*)┘    reads      stores    Logs dashboard
                                  labels             + Explore
```

## Turning it on

Off by default. Add `logs` to `COMPOSE_PROFILES` in `.env`:

```bash
COMPOSE_PROFILES=demo,logs
```

then:

```bash
docker compose up -d --wait
```

Open Grafana and the **Logs** dashboard, in the same `labmon` folder as
the overview. It shows log volume by level, the warning and error lines
themselves, readings skipped per sensor, and an unparsed view of
everything, with a dropdown to narrow to one container.

For anything the dashboard does not answer, go to **Explore**, pick the
**Loki** datasource, and query directly:

```logql
{container="mock-room-1"}
```

Every container is collected, not only sensors — "why did the sensor stop"
is often answered by InfluxDB's or Grafana's output rather than the
sensor's own.

Stopping needs the profile too, or the two containers are left running:

```bash
COMPOSE_PROFILES=demo,logs docker compose down
```

## Retention

`LOKI_RETENTION_PERIOD` in `.env`, 30 days by default:

```bash
LOKI_RETENTION_PERIOD=168h   # a week
```

This is the setting that decides how much disk the stack uses over time —
log lines are far bulkier than measurements, so a year of logs costs more
than a year of readings.

> [!WARNING]
> **Do not set it below 24h.** That is Loki's documented minimum, and Loki
> does not enforce it: `1h` starts cleanly, is reported back by `/config` as
> `1h`, and produces no warning. It will not do what it says either, since
> retention cannot be finer than the 24h index period the schema requires —
> a setting that appears accepted and quietly means something else.

Retention only works because `loki/config.yaml` enables the compactor. With
`retention_enabled` off — Loki's default — the retention period is silently
ignored and logs accumulate until the disk fills.

## What a sensor line looks like

Sensors emit [logfmt](https://brandur.org/logfmt): named fields rather
than prose, so a collector can pick out what it needs and a person can
still read the line.

```
ts=2026-08-04T09:31:07.412+00:00 level=info logger=labmon.sensors.loop \
  msg="wrote readings" readings=7 sensor_id=room-1 skipped=0 window_s=30
```

Query on the fields in Grafana:

```logql
{container=~".+"} | logfmt | level="warning"
{container=~".+"} | logfmt | sensor_id="cryo-77k"
```

> [!IMPORTANT]
> **Severities are not spelled the same across the stack.** Python's logging
> module produces `warning` and `critical`; the Go services — Grafana, Loki
> and Alloy — produce `warn` and `fatal`. A filter naming one spelling
> silently drops the other half, which reads as a quiet stack rather than a
> missed filter.

Match both:

```logql
{container=~".+"} | logfmt | level=~"warn|warning"
```

The Logs dashboard's panels already do.

### What is logged, and at what level

**Readings are DEBUG, and off by default.** At 100 Hz across a handful of
channels they are hundreds of lines a second, and Loki would end up
storing a second copy of what InfluxDB already holds far more compactly.

**Events are INFO**, each carrying `sensor_id`: startup, the periodic
summary every 30s, every warning and every error. Those are what you
correlate against a flat trace — "why did this stop" is answered by a
warning, not by the last normal reading.

`skipped` counts readings that arrived and could not be written, which
today means a conversion produced `nan` or `inf` — a formula undefined
over part of its range, or a factor large enough to overflow. The first
one on a channel also logs a warning naming the sensor; after that the
count is the only report, so a channel producing nothing else stays
visible without a line per reading:

```logql
{container=~".+"} | logfmt | skipped > 0
```

A sensor appears in the summary even when it wrote nothing at all. Each loop
declares the sensors it is responsible for — from the calibration file, for
`serial-sensor` — so a channel that has never said a word still reports
`readings=0 skipped=0` every window. An instrument that is powered off, a
board that has stopped talking and a process that has died are three
different situations, and only the last produces no line at all.

To see every reading while bringing a board up:

```bash
uv run labmon mock-sensor --measurement temperature --unit "°C" \
  --log-level debug
uv run labmon serial-sensor --port … --calibration … --log-level debug
```

## Where it collects from

Alloy reads container output through the Docker API. That means anything a
container writes to stdout or stderr is collected, with no logging library,
no configuration, and no change to the container.

The label `container` carries the container's name. `docker compose logs`
still works exactly as before and is often quicker for a glance; Loki is
for history, for correlating two containers, and for looking at a machine
you are not sitting in front of.

### The `sensor_id` label

A reading in InfluxDB is tagged `sensor_id`. Its log lines carry the same
label, so the two sides are queried by the same identifier:

```logql
{sensor_id="cryo-diode"}
```

Alloy reads it out of the line rather than off the container, with a
`logfmt` stage feeding a `labels` stage. That distinction is the whole
point. A container label would describe a *process*, while `sensor_id`
describes a *reading*, and the two only coincide while one container runs
one sensor. `serial-sensor` breaks that: one process reads a calibration
file covering several channels and reports each under its own id, so the
demo's `demo-serial-sensor` container produces six labelled streams, one
per channel in `demo/calibration.demo.toml`.

Because the label comes from the line, renaming a channel in a
calibration file changes it with no other edit — there is no second copy
in `docker-compose.yml` to drift out of sync.

A line with no `sensor_id` field — InfluxDB's, Grafana's, Alloy's own —
gets no such label at all, rather than an empty one. An empty value would
be a stream of its own and would appear in every label browser in
Grafana.

<details>
<summary><b>Why InfluxDB's WAL flush line is turned down</b></summary>

<br>

A write does not go straight into InfluxDB's long-term storage. It is
validated in memory and appended to a **write-ahead log** — a WAL, an
append-only file that makes the write durable before the slower work of
organising it into queryable form happens. Once a second by default,
InfluxDB flushes that buffer to the object store and logs a line saying
so ([InfluxDB 3 Core
internals](https://docs.influxdata.com/influxdb3/core/reference/internals/durability/)).

That once-a-second interval is the same one that makes a single
unbatched write cost about a second, which is why `PointWriter` batches
at all — [`docs/latency.md`](latency.md#why-the-influxdb-write-costs-a-second)
has the measurements.

One line per second is 86,400 a day and around 2.6 million over the
default 30-day retention. At steady state it was *everything* InfluxDB
emitted, so with the `logs` profile on it was the entire stored log.

It is lowered rather than dropped, with a `--log-filter` directive in
`docker-compose.yml`. The line is genuinely diagnostic — it carries
`n_ops` and `wal_file_number` — so it comes back by raising InfluxDB's
level for that module, and `debug` restores it in full.

Lowering one module's level is a blunt instrument: what is worth keeping for
a day and worthless after thirty is a general problem, and suppression is
the wrong shape for it. A retention tier for noisy-but-useful signals is
tracked separately.

</details>

### If a container's output never appears

Almost always **buffering**, not collection. Python block-buffers stdout
when it is not a terminal, which it never is in a container, so a program
printing a short line every few seconds can fill a buffer for twenty
minutes before anything is written. `docker compose logs` shows nothing
either, which is the giveaway that the problem is upstream of Alloy.

labmon's own image sets `PYTHONUNBUFFERED=1` for this reason. A container
built from something else may need the same, or to log through `logging`
rather than `print`.

## Logs from other machines

Alloy collects from the host it runs on, so a sensor on a client machine
is invisible to the server's collector. Such a client runs its own Alloy,
which ships to the server rather than to a Loki of its own — one place to
look, with the same labels either way.

That needs a way into Loki from outside the compose network, and Loki is
deliberately not published. The route is the `tls` profile's proxy, on a
third port:

```
client Alloy ──https──► caddy:3444 ──http──► loki:3100
                        (basic auth)          (never published)
```

Two things are true of that endpoint and not of the other two the proxy
serves. It **authenticates**, and it **only accepts pushes**.

Authentication is not symmetry for its own sake. InfluxDB and Grafana
check a credential of their own behind the proxy, so TLS there only has
to keep one from being read in transit. Loki's push API checks nothing,
so proxying it bare would put a write-anything endpoint on the LAN —
anything that could reach it could fill the log store, or bury a real
line under invented ones.

Restricting the path limits what a leaked credential is worth. It is
copied to every machine that ships logs, so it should buy writing log
lines and nothing more; the same host and port answers 404 to every
other request, including the query API that would otherwise read back
everything the stack has collected.

### On the server

Generate a credential and put the hash in `.env`:

```bash
docker compose exec caddy caddy hash-password
```

That prompts for a password and prints a bcrypt hash. The hash goes in
`LABMON_LOKI_PUSH_HASH`, the password goes to each client, and
`LABMON_TLS_LOKI_SITES` lists the addresses clients dial — the same form
as the other two lists in `.env.example`.

> [!CAUTION]
> **Single-quote the hash**, exactly as `.env.example` has it:
>
> ```dotenv
> LABMON_LOKI_PUSH_HASH='$2a$14$...'
> ```
>
> Bcrypt hashes are full of `$`, and compose expands an unquoted `$name` in
> `.env` as a variable — an unset one, so that span of the hash is replaced
> by nothing and every push is refused with no indication why. Single quotes
> suppress it; double quotes do not.

Until that is set the endpoint refuses every credential. The hash shipped
in `docker-compose.yml` is bcrypt of random bytes nobody kept, so no
password matches it: publishing the port does not open anything.

### On the client

See [`docs/client-setup.md`](client-setup.md#shipping-this-machines-logs).

### What arrives

The same labels a local container gets, plus `host`, which names the
machine the line came from:

```logql
{host="pi-optics-bench"}
{sensor_id="cryo-diode"}
```

The second finds that sensor's lines wherever it runs — which is the
point of labelling by reading rather than by container.

## Sensors that run under systemd

A sensor that cannot run in a container runs under systemd instead (see
[`deploy/`](../deploy/)), and writes to the journal rather than through the
Docker API. Alloy collects those too, so both deployment shapes end up in
the same place:

```logql
{unit="labmon-custom-sensor.service"}
```

Both system units and user units work. A user unit — one installed with
`systemctl --user`, needing no root — names the manager rather than itself
in its systemd unit field, reporting `user@1000.service` for every one of
them; its real name is recorded separately, and that is what gets used.

### Only labmon's own units

The host journal is the whole machine: Docker, containerd, the network
manager, every login session. On the machine this was developed on that was
775 MB against a handful of sensor lines, so collecting all of it would
dwarf everything else in Loki and centralise system logs nobody asked to
centralise.

`alloy/config.alloy` therefore keeps only units whose name starts with
`labmon-`. Widen that regex to collect more; `.*` collects the machine.

### About the `level` label

Journal entries carry a `level`, which container logs have no equivalent of.

> [!NOTE]
> It is less than it appears: **everything a plain script writes arrives as
> `info`, stderr included.** systemd does not infer severity from the stream
> — only an explicit `<N>` prefix on the line changes it. So `level` is a
> hook for real severities rather than a source of them.

## What Alloy needs, and what that costs

Alloy mounts `/var/run/docker.sock`, plus the host journal read-only.

> [!WARNING]
> **Access to that socket is equivalent to root on the host** — it allows
> starting a container with the host filesystem mounted. Marking the mount
> `read_only` does not meaningfully constrain it, since the socket is a full
> API either way.

That is the trade for collecting logs without modifying every container.
Reasonable on a dedicated lab server already running the stack as a whole;
less so on a shared or multi-tenant machine.

If it is the wrong trade for yours:

- Put a socket proxy in front, restricting Alloy to `GET /containers`.
- Or drop Alloy and use Docker's `loki` logging driver, which sends output
  straight from the daemon and needs no socket access. It is a host-level
  plugin install, and it cannot read journald or files, which is why it is
  not the default here.

## When something is missing

Alloy has a UI at [http://localhost:12345](http://localhost:12345) showing
every component, its health, and what it last sent. Check there before
suspecting Loki: a component in a failed state names its own problem.

It is bound to loopback, since it has no authentication and lists
component configuration, endpoints and recently sent data. Setting
`LABMON_BIND_ADDRESS` does not publish it, so administering the server
from a workstation means forwarding it:

```bash
ssh -L 12345:127.0.0.1:12345 <server-host>
```

That is the same instruction [`docs/client-setup.md`](client-setup.md)
gives for a client's UI, which is bound the same way.

Loki itself has no healthcheck in `docker-compose.yml`, unlike every other
service. Its image is distroless — it contains exactly one binary and no
shell, `wget` or `curl` for a check to use. Alloy retries a failed push, so
the few seconds before Loki is ready cost nothing.

## What is not here yet

- **Devices that speak syslog.** Instruments, switches and UPS units that
  can be pointed at a syslog collector — Alloy has a listener for it,
  unconfigured for want of a device to test against.
