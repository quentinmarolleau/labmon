# Custom sensor template

A starting point for reading an instrument through a manufacturer's API —
a Python SDK, a REST endpoint, a CLI — and writing the result to InfluxDB
alongside every other sensor.

Full guide: [`docs/custom-sensor.md`](../../docs/custom-sensor.md).

## Which file to start from

|  | Read whenever you like | Rate-limited, billed, or slow |
| --- | --- | --- |
| **Script** | `sensor_continuous.py` | `sensor_triggered.py` |
| **Service** | `compose.snippet.yml`, first service | `docker compose run --rm` |

Continuous is the common case: a process stays up and reads on an
interval. Choose triggered when something external should decide *when* a
reading happens — an API that charges per call, refuses more than a few
requests an hour, or takes minutes to answer.

Either script also runs without Docker, on a machine where the container
is not an option — see [`deploy/`](../../deploy/) for the systemd units
and when they are worth reaching for.

## This template and the repository's own Dockerfile

There are two Dockerfiles, and they stack rather than compete:

```
python:3.14-slim  ->  labmon:latest  ->  my-cryostat-sensor
                      (repo root)        (your copy of this template)
                                     ->  my-gauge-sensor
```

The one at the repository root builds `labmon:latest`, which every labmon
service already runs — the mock sensors, `serial-sensor`, the demo feeder,
the client stack. **Do not add your instrument's dependencies there.**
Three reasons, in order of how soon they bite:

- It is version-controlled, so the edit collides with every `git pull`.
- It is shared, so a vendor SDK that fails to install — an unreachable
  licence server, a wrong architecture — stops the whole stack building,
  including the sensors that have nothing to do with your instrument.
- Two instruments whose SDKs pin incompatible dependencies cannot share
  one image at all. A layer each, and they never have to agree.

The same applies to this directory. Copy it somewhere of your own before
editing, or your changes are in git's way for the same reason.

## Getting started

The template runs before you have written any instrument code:
`read_value()` returns a simulated value. Start it, confirm readings reach
Grafana, and only then swap in the real device — so that if it breaks
afterwards, you know it is the instrument and not the configuration.

1. Copy this directory somewhere of your own.
2. Build the base image once from the repo root: `docker compose build`.
3. `docker build -t my-sensor .`
4. Paste the service from `compose.snippet.yml` and `docker compose up -d my-sensor`.
5. Replace `read_value()` and the `CHANGE-ME` identifiers.

## What you do not have to write

`labmon.sensors.polling` already handles the batching, the InfluxDB
connection, `SIGINT`/`SIGTERM` shutdown, and — the part worth not
rewriting — **retry with backoff around a failing read**. An instrument
that throws at 03:00 is logged with its traceback and retried, not fatal.

Do not catch exceptions inside `read_value()` to keep the process alive.
That already happens, and swallowing them turns a broken instrument into a
flat line nobody investigates.
