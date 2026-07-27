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

`docker-compose.client.yml` has one `sensor` service as a template —
edit its `command` to describe the sensor actually attached to this
device (see [`docs/mock-sensor.md`](mock-sensor.md) for the available
flags). Running more than one sensor on the same device: copy the
service block, give the copy its own name/`container_name`, and edit its
`command`.

## Option 2: Bare install (no Docker)

Better suited to a resource-constrained device, or if you'd rather not
run a container runtime at all.

```bash
git clone <this repo> && cd labmon
uv sync --no-dev
cp .env.client.example .env   # fill in INFLUXDB_HOST and INFLUXDB3_AUTH_TOKEN
set -a && source .env && set +a
uv run mock-sensor --sensor-id=CHANGE-ME --measurement=temperature --setpoint=21 --noise=0.15 --unit=°C
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
