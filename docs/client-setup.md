# Setting up a client

A **client** is any machine that pushes readings to a labmon server over
the network — typically a Raspberry Pi or a lab PC with an instrument
wired to it.

```
   THIS MACHINE                                    THE SERVER
  ┌────────────────────────────────┐             ┌──────────────────────┐
  │  instrument                    │             │                      │
  │      │ USB / vendor API        │  readings   │  InfluxDB      :8181 │
  │      ▼                         │ ───────────►│                      │
  │  labmon serial-sensor          │             │  Grafana       :3000 │
  │      or mock-sensor            │             │                      │
  │                                │  log lines  │  Loki          :3444 │
  │  Alloy  (optional) ────────────┼────────────►│  (logs profile)      │
  └────────────────────────────────┘             └──────────────────────┘
```

The server side — publishing its ports, issuing the token — is
[`deployment.md`](deployment.md). This page is the client.

## What it needs to know

Three values, copied from the server
[out of band](deployment.md#one-token-and-it-is-an-admin-token):

| Setting | Is | Example |
|---|---|---|
| `INFLUXDB_HOST` | where the server is | `http://192.168.1.50:8181` |
| `INFLUXDB_DATABASE` | which database to write to | `lab` |
| `INFLUXDB3_AUTH_TOKEN` | the admin token InfluxDB issued | `apiv3_…` |

Two more if the server runs the `tls` profile — see
[Connecting over TLS](#connecting-over-tls).

> [!TIP]
> Start with `mock-sensor` rather than the real thing. It invents a reading
> and talks to no hardware, so once one shows up in Grafana the address,
> token and database name are all confirmed and the only thing left to get
> wrong is the instrument. [Switching to real
> hardware](#switching-to-real-hardware) is one word in a command.

## Option 1 — Docker

Reuses the same `Dockerfile` as the server, built natively on the client. A
Raspberry Pi builds its own `linux/arm64` image directly; there is no
cross-compilation and no registry.

```bash
git clone https://github.com/quentinmarolleau/labmon.git && cd labmon
cp .env.client.example .env      # fill in INFLUXDB_HOST and INFLUXDB3_AUTH_TOKEN
docker compose -f docker-compose.client.yml build
docker compose -f docker-compose.client.yml up -d --wait
```

<details>
<summary><b>Running several sensors on one machine</b></summary>

<br>

`docker-compose.client.yml` has one `sensor-1` service plus a commented-out
`sensor-2` showing the pattern. Give each its own service extending the
shared `x-labmon-sensor` anchor, its own `container_name`, and a `command`
with a distinct `--sensor-id`.

</details>

## Option 2 — bare install, no Docker

Better on a resource-constrained device, or where a container runtime is
unwelcome.

```bash
uv tool install labmon
mkdir -p ~/.config/labmon
curl -o ~/.config/labmon/sensor.env \
  https://raw.githubusercontent.com/quentinmarolleau/labmon/main/.env.client.example
# fill in INFLUXDB_HOST and INFLUXDB3_AUTH_TOKEN
set -a && source ~/.config/labmon/sensor.env && set +a
labmon mock-sensor --sensor-id=CHANGE-ME --measurement=CHANGE-ME --unit=CHANGE-ME
```

No repository. The sensor commands are a client, and `uv tool install`
puts `labmon` on the PATH. Serial support is part of the base install;
add an extra only for what the machine actually does —
`labmon[spline]` for spline calibration, `labmon[tui]` to run
`labmon monitor` on the same box.

To survive a reboot, use the example unit at
[`deploy/labmon-sensor.service`](../deploy/labmon-sensor.service) — edit
`User` and `ExecStart` for your setup:

```bash
curl -O https://raw.githubusercontent.com/quentinmarolleau/labmon/main/deploy/labmon-sensor.service
sudo cp labmon-sensor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now labmon-sensor.service
```

The unit reads `~/.config/labmon/sensor.env` and runs
`~/.local/bin/labmon`, which is where `uv tool install` puts it. systemd
starts with no PATH of its own, so that has to be an absolute path.

For a second sensor on the same device, copy the unit under a different
name with its own `ExecStart`.

## Switching to real hardware

`mock-sensor` simulates a reading with a mean-reverting random walk.
`--setpoint`, `--noise` and `--log-scale` are simulation knobs, not
something to tune for a real sensor — which is why the commands above set
only `--sensor-id`, `--measurement` and `--unit`.

To read an actual board, swap `mock-sensor` for **`serial-sensor`** in the
`command:` or `ExecStart` above. Both deployment paths carry over
unchanged: `docker-compose.client.yml` has a commented-out `serial-sensor`
service, and `deploy/labmon-serial-sensor.service` is the matching systemd
unit. [`serial-sensor.md`](serial-sensor.md) has the calibration file, the
udev rule that gives the board a stable device name, and the Arduino Due
specifics.

The plumbing underneath is the same either way. `labmon.writer.PointWriter`
and `labmon.influx.get_client` take an already-built `Point` and get it to
InfluxDB reliably, with no assumption about where the value came from. Raw
counts become physical units in `labmon.calibration`, before the `Point` is
built — never in InfluxDB or Grafana.

## Connecting over TLS

Only when the server runs the `tls` profile
([setting it up there](deployment.md#encrypting-client-and-viewer-traffic)).
Two changes on this machine, whichever option above it uses.

**1. Dial the proxy rather than the database** — `https://`, and the
proxy's port:

```bash
INFLUXDB_HOST=https://192.168.1.50:8443
```

**2. Trust the server's root certificate.** Copy `labmon-ca.crt` off the
server the same way the token travelled, and name where it landed:

```bash
INFLUXDB_TLS_CA=/etc/labmon/labmon-ca.crt
```

> [!NOTE]
> Leave it world-readable — `chmod 644`, which is what `export-ca.sh`
> writes. The sensor containers run as an unprivileged user and read the
> file through a bind mount, so an owner-only copy is one they cannot open.
> There is nothing in it to protect: the private key never leaves the
> server.

The server's root is in no system trust store, so without this the sensor
**refuses to connect** rather than merely warning. One variable covers both
directions: the same file is read when writing points and when reading them
back. A path that does not exist fails at startup with a message naming the
variable, rather than surfacing later as an apparent problem with the
server's certificate.

<details>
<summary><b>Under Docker, the file also has to exist inside the container</b></summary>

<br>

`docker-compose.client.yml` carries a commented-out mount that puts it at
the same path the variable names, so one value works either way. Uncomment
it along with the variable:

```yaml
  volumes:
    - ${INFLUXDB_TLS_CA}:${INFLUXDB_TLS_CA}:ro
```

A service that declares its own `volumes:` replaces that list rather than
adding to it, which is why the commented-out `serial-sensor` service
carries the same line again.

</details>

Clients do not have to move in step with the server: its 8181 and 3000 stay
open until its operator closes them, so each machine switches when it suits.

## Shipping this machine's logs

Optional, and independent of everything above — a sensor writes readings
whether or not its logs go anywhere. With it on, this machine's logs appear
in the same Grafana as the server's, labelled by sensor, so "what was this
instrument saying when the trace went flat" is one query rather than an SSH
session.

Requires the server to run the `tls` profile with a push credential
configured — see [Logs from other
machines](logging.md#logs-from-other-machines).

Four settings, alongside the `INFLUXDB_TLS_CA` you already have:

```bash
LABMON_LOKI_URL=https://192.168.1.50:3444/loki/api/v1/push
LABMON_LOKI_PUSH_USER=labmon
LABMON_LOKI_PUSH_PASSWORD=          # copied out of band, as the token was
LABMON_CLIENT_NAME=pi-optics-bench  # names this machine in every line
                                    # (default: unnamed-client)
```

`LABMON_LOKI_PUSH_PASSWORD` is a different secret from
`INFLUXDB3_AUTH_TOKEN`, and buys much less: writing log lines to a port
that answers 404 to anything else. `INFLUXDB_TLS_CA` is reused rather than
duplicated — the collector verifies the server against the same root the
sensor does.

```bash
COMPOSE_PROFILES=logs docker compose -f docker-compose.client.yml up -d
```

Alloy reads container output through this machine's Docker socket, and the
journal for a bare install running under systemd, so both shapes are
collected the same way.

> [!WARNING]
> Access to the Docker socket is equivalent to root on this machine. That
> is the price of collecting logs without modifying each container —
> [`logging.md`](logging.md) covers the trade.

### When lines do not arrive

Alloy publishes its own UI on port 12345, showing each component's health
and what it last sent. A rejected credential shows there as a 401 against
`loki.write`, which distinguishes "the password is wrong" from "the server
cannot be reached" — worth checking before anything else.

It is bound to loopback, having no authentication and listing component
configuration and recently sent data. On the machine itself that is
[http://localhost:12345](http://localhost:12345); from anywhere else,
forward it over SSH:

```bash
ssh -L 12345:127.0.0.1:12345 <client-host>
```
