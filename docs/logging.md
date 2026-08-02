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

Loki's own minimum is 24h. This is the setting that decides how much disk
the stack uses over time — log lines are far bulkier than measurements, so
a year of logs costs more than a year of readings.

Retention only works because `loki/config.yaml` enables the compactor.
With `retention_enabled` off — Loki's default — the retention period is
silently ignored and logs accumulate until the disk fills.

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

## What Alloy needs, and what that costs

Alloy mounts `/var/run/docker.sock`. **Access to that socket is equivalent
to root on the host** — it allows starting a container with the host
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
  remote sensor machine needs its own Alloy forwarding here, which also
  means exposing Loki's push endpoint beyond this network.
- **Logs from bare-metal sensors.** A sensor run under systemd rather than
  Docker writes to the journal, which Alloy can read but is not yet
  configured to.
- **Devices that speak syslog.** Instruments, switches and UPS units that
  can be pointed at a syslog collector — Alloy has a listener for it,
  unconfigured for want of a device to test against.
