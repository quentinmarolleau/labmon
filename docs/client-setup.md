# Client setup

A client is any machine that pushes sensor data to a remote labmon
server over the LAN — e.g. a Raspberry Pi. See
[`docs/deployment.md`](deployment.md) for the server side (exposing it on
the network, distributing the auth token). This doc covers running a
sensor on the client itself, two ways.

Both need the same three values from the server: `INFLUXDB_HOST` (its LAN
IP/hostname), `INFLUXDB_DATABASE`, and `INFLUXDB3_AUTH_TOKEN`, copied out
of band — see [`docs/deployment.md`](deployment.md#distributing-the-auth-token).

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

## From mock sensor to real hardware

Both options above run `mock-sensor`, which only simulates a reading (a
mean-reverting random walk) and doesn't talk to any hardware — useful
for proving a client can reach the server before wiring anything up.
`--setpoint`/`--noise`/`--log-scale` are simulation-only knobs, not
something to "tune" for a real sensor, which is why the commands above
only set `--sensor-id`, `--measurement`, and `--unit`.

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
