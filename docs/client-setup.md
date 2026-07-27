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

There's no real acquisition script yet — both options above still run
`mock-sensor`, which only simulates a reading (a mean-reverting random
walk); it doesn't talk to any hardware. `--setpoint`/`--noise`/
`--log-scale` are simulation-only knobs, not something to "tune" for a
real sensor — ignore them here, which is why the commands above only set
`--sensor-id`, `--measurement`, and `--unit`.

Reading a real microcontroller (e.g. an Arduino over serial) is a
separate, later piece of work, but the plumbing already in place is
designed to be reused as-is by whatever that script ends up being:
- `labmon.writer.PointWriter` and `labmon.influx.get_client` are
  generic — they just take an already-built `Point` (measurement, tags,
  a field value, a timestamp) and get it to InfluxDB reliably. Nothing
  about them assumes the value came from a simulation.
- The `--unit` tag is written as-is, with no conversion — so whatever
  writes it must already hold a value in physical units. That means raw
  sample → physical unit conversion (e.g. raw ADC counts/volts from the
  microcontroller → kelvin via a calibration curve) belongs in that
  future acquisition script, before it constructs the `Point` — not in
  InfluxDB or Grafana. A real script would replace `mock-sensor` in the
  `command:`/`ExecStart` above with something like `--device
  /dev/ttyACM0 --calibration cal.json`, still writing through the same
  `PointWriter`.

No name is settled for that future script yet ("grabber," "acquirer,"
and "reader" all fit) — that can wait until it's actually being built.
