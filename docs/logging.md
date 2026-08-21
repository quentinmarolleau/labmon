# Logs

Measurements say a reading stopped arriving. They never say why. The
reasons live in a container's output — an expired API token, an instrument
reporting busy, a traceback from a vendor SDK — and by default that is only
reachable by running `docker compose logs` on the right machine, with no
history beyond what Docker happens to still hold.

The `logs` profile adds two containers that fix this: **Loki** stores log
lines, and **Alloy** collects them from every container on the host and
ships them over. Both are Grafana's own, so the result is queryable in the
same Grafana that shows the measurements.

## Turning it on

Off by default. Add `logs` to `COMPOSE_PROFILES` in `.env`:

```bash
COMPOSE_PROFILES=demo,logs
```

then:

```bash
docker compose up -d --wait
```

Open Grafana, go to **Explore**, pick the **Loki** datasource, and query:

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

**Do not set it below 24h.** That is Loki's documented minimum, but Loki
does not enforce it: `1h` starts cleanly, is reported back by `/config`
as `1h`, and produces no warning. It will not do what it says either, since
retention cannot be finer than the 24h index period the schema requires.
The result is a setting that appears to have been accepted and quietly
means something else.

Retention only works because `loki/config.yaml` enables the compactor.
With `retention_enabled` off — Loki's default — the retention period is
silently ignored and logs accumulate until the disk fills.

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

A sensor appears in the summary even when it wrote nothing at all, which
is the case worth seeing: the trace is flat, and this line is the only
thing that distinguishes readings arriving unwritable from a sensor that
died.

To see every reading while bringing a board up:

```bash
uv run mock-sensor --log-level debug
uv run serial-sensor --port … --calibration … --log-level debug
```

## Where it collects from

Alloy reads container output through the Docker API. That means anything a
container writes to stdout or stderr is collected, with no logging library,
no configuration, and no change to the container.

The label `container` carries the container's name. `docker compose logs`
still works exactly as before and is often quicker for a glance; Loki is
for history, for correlating two containers, and for looking at a machine
you are not sitting in front of.

### If a container's output never appears

Almost always buffering rather than collection. Python block-buffers stdout
when it is not a terminal, which it never is in a container, so a program
printing a short line every few seconds can fill a buffer for twenty
minutes before anything is written. `docker compose logs` shows nothing
either, which is the giveaway that the problem is upstream of Alloy.

labmon's own image sets `PYTHONUNBUFFERED=1` for this reason. A container
built from something else may need the same, or to log through `logging`
rather than `print`.

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

Journal entries carry a `level`, which container logs have no equivalent
of. It is less than it appears: **everything a plain script writes arrives
as `info`, stderr included.** systemd does not infer severity from the
stream — only an explicit `<N>` prefix on the line changes it.

So `level` is a hook for real severities rather than a source of them.

## What Alloy needs, and what that costs

Alloy mounts `/var/run/docker.sock`, plus the host journal read-only.
**Access to that socket is equivalent to root on the host** — it allows starting a container with the host
filesystem mounted. Marking the mount `read_only` does not meaningfully
constrain it, since the socket is a full API either way.

This is the honest trade for collecting logs without modifying every
container. It is a reasonable one on a dedicated lab server that is already
running the stack as a whole. It is less reasonable on a shared or
multi-tenant machine.

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

Loki itself has no healthcheck in `docker-compose.yml`, unlike every other
service. Its image is distroless — it contains exactly one binary and no
shell, `wget` or `curl` for a check to use. Alloy retries a failed push, so
the few seconds before Loki is ready cost nothing.

## What is not here yet

- **Logs from other machines.** Alloy collects from the host it runs on. A
  sensor on a *different* machine needs its own Alloy forwarding here, which
  also means exposing Loki's push endpoint beyond this network.
- **Devices that speak syslog.** Instruments, switches and UPS units that
  can be pointed at a syslog collector — Alloy has a listener for it,
  unconfigured for want of a device to test against.
