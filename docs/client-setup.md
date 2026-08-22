# Client setup

A client is any machine that pushes sensor data to a remote labmon
server over the LAN — e.g. a Raspberry Pi. See
[`docs/deployment.md`](deployment.md) for the server side (exposing it on
the network, distributing the auth token). This doc covers running a
sensor on the client itself, two ways.

Both need the same three values from the server: `INFLUXDB_HOST` (its LAN
IP/hostname), `INFLUXDB_DATABASE`, and `INFLUXDB3_AUTH_TOKEN`, copied out
of band — see [`docs/deployment.md`](deployment.md#distributing-the-auth-token).
A server running the `tls` profile needs two more, which apply to either
option below — see [Connecting over TLS](#connecting-over-tls).

Docker or bare install is only a packaging choice; either can run either
sensor script. The examples below start with `mock-sensor` because it
proves the network path works before any hardware is involved — see
[From mock sensor to real hardware](#from-mock-sensor-to-real-hardware)
for the switch to `serial-sensor`.

## Option 1: Docker

Reuses the same `Dockerfile` as the local demo, built natively on the
client — a Raspberry Pi builds its own `linux/arm64` image directly,
consistent with this project's "build locally, no registry" pattern
(there's no cross-compilation or CI publishing step involved).

```bash
git clone <this repo> && cd labmon
cp .env.client.example .env   # fill in INFLUXDB_HOST and INFLUXDB3_AUTH_TOKEN
docker compose -f docker-compose.client.yml build
docker compose -f docker-compose.client.yml up -d --wait
```

`docker-compose.client.yml` has one `sensor-1` service as a template,
plus a commented-out `sensor-2` showing the pattern for running several
sensors on the same device in parallel: give each its own service
extending the shared `x-labmon-sensor` anchor, its own `container_name`,
and a `command` with a distinct `--sensor-id` (see
[`docs/mock-sensor.md`](mock-sensor.md) for the available flags — and the
note below on which of those flags actually matter here).

## Option 2: Bare install (no Docker)

Better suited to a resource-constrained device, or if you'd rather not
run a container runtime at all.

```bash
git clone <this repo> && cd labmon
uv sync --no-dev
cp .env.client.example .env   # fill in INFLUXDB_HOST and INFLUXDB3_AUTH_TOKEN
set -a && source .env && set +a
uv run mock-sensor --sensor-id=CHANGE-ME --measurement=CHANGE-ME --unit=CHANGE-ME
```

To have it run in the background and survive a reboot, use the example
systemd unit at [`deploy/labmon-sensor.service`](../deploy/labmon-sensor.service):
edit the paths, `User`, and `ExecStart` command for your setup, then:

```bash
sudo cp deploy/labmon-sensor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now labmon-sensor.service
```

Running more than one sensor on this device: copy the unit file under a
different name (e.g. `labmon-sensor-2.service`) with its own `ExecStart`.

## Connecting over TLS

Only when the server runs the `tls` profile — see
[`docs/deployment.md`](deployment.md#encrypting-client-and-viewer-traffic)
for turning it on there. Two things change on this machine, and neither
depends on which of the two options above it uses.

**Dial the proxy rather than the database**, with an `https://` scheme
and the proxy's port:

```bash
INFLUXDB_HOST=https://192.168.1.50:8443
```

**Trust the server's root certificate.** Copy `labmon-ca.crt` off the
server the same way the token travels — out of band, by a route that
reliably delivers the right file — and name where it landed:

```bash
INFLUXDB_TLS_CA=/etc/labmon/labmon-ca.crt
```

The server signs its own certificates, and a private root is in no system
trust store, so without this the sensor refuses to connect rather than
merely warning. One variable is enough for both directions of traffic:
the same file is read when writing points and when reading them back. A
path that does not exist fails at startup with a message naming the
variable, rather than surfacing later as an apparent problem with the
server's certificate.

Under Docker the file also has to exist *inside* the container.
`docker-compose.client.yml` carries a commented-out mount that puts it at
the same path the variable names, so one value works either way —
uncomment it along with the variable:

```yaml
  volumes:
    - ${INFLUXDB_TLS_CA}:${INFLUXDB_TLS_CA}:ro
```

A service that declares its own `volumes:` replaces that list rather than
adding to it, which is why the commented-out `serial-sensor` service
carries the same line again.

There is no need to move in step with the server. Its 8181 and 3000 stay
open until its operator closes them, so each client switches over
whenever it suits.

## Shipping this machine's logs

Optional, and independent of everything above: a sensor writes readings
whether or not its logs go anywhere. Turning this on means the machine's
logs appear in the same Grafana as the server's, labelled by sensor, so
"what was this instrument saying when the trace went flat" is one query
rather than an ssh session.

It requires the server to be running the `tls` profile with a push
credential configured — see [Logs from other
machines](logging.md#logs-from-other-machines) for that side.

Four settings, alongside the `INFLUXDB_TLS_CA` you already have:

```bash
LABMON_LOKI_URL=https://192.168.1.50:3444/loki/api/v1/push
LABMON_LOKI_PUSH_USER=labmon
LABMON_LOKI_PUSH_PASSWORD=          # copied out of band, as the token was
LABMON_CLIENT_NAME=pi-optics-bench  # names this machine in every line
                                    # (default: unnamed-client)
```

`LABMON_LOKI_PUSH_PASSWORD` is a different secret from
`INFLUXDB3_AUTH_TOKEN` and buys much less: writing log lines, to a port
that answers 404 to anything else. `INFLUXDB_TLS_CA` is reused rather
than duplicated — the collector verifies the server against the same root
the sensor does.

Then start the collector:

```bash
COMPOSE_PROFILES=logs docker compose -f docker-compose.client.yml up -d
```

It reads container output through this machine's Docker socket, and the
journal for a bare install running under systemd, so both shapes are
collected the same way. Access to the Docker socket is equivalent to root
here, which is the price of collecting logs without modifying each
container — [`docs/logging.md`](logging.md) covers that trade.

### When lines do not arrive

Alloy publishes its own UI on port 12345, which shows each component's
health and what it last sent. A rejected credential shows there as a 401
against `loki.write`, which is the difference between "the password is
wrong" and "the server cannot be reached" — and worth checking before
anything else.

It is bound to loopback, since it has no authentication and lists
component configuration and recently sent data. On the machine itself
that is [http://localhost:12345](http://localhost:12345); from anywhere
else, forward it over SSH:

```bash
ssh -L 12345:127.0.0.1:12345 <client-host>
```

## From mock sensor to real hardware

`mock-sensor` only simulates a reading (a mean-reverting random walk) and
doesn't talk to any hardware. `--setpoint`/`--noise`/`--log-scale` are
simulation-only knobs, not something to "tune" for a real sensor, which
is why the commands above only set `--sensor-id`, `--measurement`, and
`--unit`. Once a reading appears in Grafana, the client's network path,
token, and database name are all confirmed working.

To read an actual board, swap `mock-sensor` for **`serial-sensor`** in
the `command:`/`ExecStart` above — see
[`docs/serial-sensor.md`](serial-sensor.md) for the calibration file,
the udev rule that gives the board a stable device name, and the
Arduino Due specifics. Both deployment paths carry over unchanged:
`docker-compose.client.yml` has a commented-out `serial-sensor` service,
and `deploy/labmon-serial-sensor.service` is the matching systemd unit.

The plumbing underneath is shared either way:
`labmon.writer.PointWriter` and `labmon.influx.get_client` just take an
already-built `Point` and get it to InfluxDB reliably, with no
assumption about where the value came from. Raw → physical unit
conversion happens in `labmon.calibration`, before the `Point` is
constructed — never in InfluxDB or Grafana.
