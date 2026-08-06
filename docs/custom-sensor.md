# Custom sensors

Reading an instrument through a manufacturer's API — a Python SDK, a REST
endpoint, a CLI — and writing the result alongside every other sensor.

This is the third acquisition path. [`mock-sensor`](mock-sensor.md) invents
readings and [`serial-sensor`](serial-sensor.md) reads a board over a
serial line; this one covers everything that arrives already in physical
units, from a vendor's own software.

The starting point is [`templates/custom-sensor/`](../templates/custom-sensor/).

## The two shapes

A sensor is driven in one of two ways, and the choice is about who decides
when a reading happens.

**Continuous** — a process stays up and reads on an interval. Right for an
instrument you can talk to as often as you like. This is `poll()`, run by
`sensor_continuous.py`.

**Triggered** — a process reads once, writes, and exits, started by
something else. Right for an API that is rate-limited, billed per call, or
slow enough that a process sleeping between calls is the wrong shape. This
is `write_reading()`, run by `sensor_triggered.py` from
`docker compose run --rm`, cron, or a systemd timer.

Polling a per-call-billed API every five seconds is the mistake this
distinction exists to prevent.

## Writing the sensor

Replace one function:

```python
from labmon.sensors.polling import poll


def read_value() -> float | None:
    return device.read_temperature()


poll(read_value, sensor_id="cryo-1", measurement="temperature", unit="K", interval=5.0)
```

`read_value` returns a value in physical units, or `None` to skip this
tick — an instrument still warming up, or with nothing new since the last
call. `None` is not an error and is not logged as one.

### Let it raise

**Raising is the correct way to report a failure.** The exception is logged
with its traceback and the read is retried with backoff, growing to a cap
so a switched-off instrument is not hammered all night, and resetting on
the first success. The process does not stop.

Do not wrap the body in `try/except` to keep things running. That is
already handled, and swallowing an error turns a broken instrument into a
flat line — the failure mode nobody notices until a week of data is
missing.

The backoff defaults to one second, doubling to a minute. An instrument
with a known recovery time should be told it rather than left to guess:

```python
poll(
    read_value,
    sensor_id="cryo-1",
    measurement="temperature",
    initial_backoff=30.0,
    max_backoff=300.0,
)
```

`poll` and `PointWriter` take a handful of other parameters, all listed
in [`docs/configuration.md`](configuration.md#library-parameters).

### Recording more than one number

`build_point()` is public for a device read that yields several values at
once:

```python
from labmon.sensors.polling import build_point

writer.write(
    build_point(temperature, sensor_id="cryo-1", measurement="temperature", unit="K")
)
writer.write(
    build_point(pressure, sensor_id="cryo-1", measurement="pressure", unit="mbar")
)
```

Same tag conventions, so the readings sit alongside every other sensor's
rather than in a shape of their own.

## Where it runs

In a container, built from the template's `Dockerfile` and started by the
service in `compose.snippet.yml`. That pins the SDK version next to the
code that uses it, needs no Python on the host, and has its output
collected without further setup.

That Dockerfile layers on the repository's own rather than replacing it:
the root `Dockerfile` builds `labmon:latest`, which every service already
runs, and your instrument's dependencies go in your copy of the template
on top of it. Putting them in the shared image instead means a failed SDK
install stops the whole stack building, and a merge conflict on every
`git pull`.

On a machine where the container is not an option — a locked-down control
PC, or an SDK that will not containerise — the same two scripts run
directly under systemd instead. See [`deploy/`](../deploy/) for the units
and the two mistakes that catch people out.

## Prove the plumbing first

`read_value()` ships returning a simulated value, so the template runs
before any instrument code exists. Start it, confirm readings reach
Grafana, then swap in the real device. If it breaks after that, the
configuration is already known good.

## Why a triggered sensor blocks for a second

`write_reading()` does not return until InfluxDB has stored the reading,
which costs about a second while its write-ahead log flushes (see
[latency](latency.md)).

That is deliberate. The alternative — handing the point to the background
writer the continuous path uses — returns immediately and loses the
reading, because the process exits before the writer thread sends
anything. A triggered sensor has no time to be asynchronous in.
