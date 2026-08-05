"""Write readings from a sensor this package knows nothing about.

`mock-sensor` invents readings and `serial-sensor` reads a board over a
serial line. Neither helps with the commonest case in a real lab: an
instrument behind a manufacturer's Python SDK, REST endpoint or CLI, which
already hands back a value in physical units. What that leaves is the same
boilerplate every time — build a client, batch the writes, catch a shutdown
signal, and survive the vendor API having a bad afternoon.

Two entry points, because a sensor is driven in one of two ways:

`poll()` runs forever, reading every `interval` seconds. This is the
right shape for an instrument you can talk to as often as you like, and it
is what a container or a systemd service runs.

`write_reading()` writes one value and returns. This is for an API that is
rate-limited, billed per call, or simply slow — polled every fifteen
minutes by a systemd timer or cron, rather than by a process that spends
899 of every 900 seconds asleep.

Example, in full:

    from labmon.sensors.polling import poll
    import vendor_sdk

    device = vendor_sdk.Device("192.0.2.10")

    poll(
        lambda: device.read_temperature(),
        sensor_id="cryo-1",
        measurement="temperature",
        unit="K",
        interval=5.0,
    )

Requires INFLUXDB3_AUTH_TOKEN to be set (e.g. via .env / direnv).
"""

import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from influxdb_client_3 import Point

from labmon.influx import INFLUXDB_DATABASE, get_client
from labmon.sensors.loop import DEFAULT_SUMMARY_INTERVAL_SECONDS, SensorLoop

logger: logging.Logger = logging.getLogger(__name__)

# A read that raises backs off instead of retrying at the nominal interval,
# so an instrument that is switched off is not hammered all night. Capped so
# recovery is still noticed promptly once it comes back. Defaults rather
# than fixed constants: an instrument whose reboot takes a known minute
# should be told that, not guessed at.
DEFAULT_INITIAL_BACKOFF_SECONDS = 1.0
DEFAULT_MAX_BACKOFF_SECONDS = 60.0


def build_point(
    value: float,
    *,
    sensor_id: str,
    measurement: str,
    unit: str = "",
    field: str = "value",
    tags: Mapping[str, str] | None = None,
) -> Point:
    """Build the point a reading becomes, without writing it.

    Exposed so a sensor with something unusual to record — several fields
    from one device read, say — can build on the same tag conventions
    rather than inventing its own.
    """
    point = Point(measurement).tag("sensor_id", sensor_id)
    # An empty unit tag is not the same as no unit tag: it would split the
    # series in two, and the halves would look identical in a legend.
    if unit:
        point = point.tag("unit", unit)
    for name, tag_value in (tags or {}).items():
        point = point.tag(name, tag_value)
    return point.field(field, value).time(datetime.now(UTC), write_precision="ms")


def write_reading(
    value: float,
    *,
    sensor_id: str,
    measurement: str,
    unit: str = "",
    field: str = "value",
    tags: Mapping[str, str] | None = None,
) -> None:
    """Write a single reading, and do not return until it is gone.

    Deliberately not `PointWriter`. That queues a point for a background
    thread, which is exactly right when a process stays up long enough to
    drain it — and exactly wrong here, where the caller reads once and
    exits. A daemon thread dies with the interpreter, taking the unwritten
    point with it.

    The cost is that this blocks for about a second while InfluxDB flushes
    its WAL (see docs/latency.md). That is the price of the reading
    actually being stored, and it is paid once per run rather than once per
    reading.
    """
    client = get_client()
    try:
        client.write(
            [
                build_point(
                    value,
                    sensor_id=sensor_id,
                    measurement=measurement,
                    unit=unit,
                    field=field,
                    tags=tags,
                )
            ]
        )
    finally:
        # In a finally so a timer-driven script does not leak a connection
        # every time the server is unreachable.
        client.close()


def poll(
    read: Callable[[], float | None],
    *,
    sensor_id: str,
    measurement: str,
    unit: str = "",
    interval: float = 5.0,
    field: str = "value",
    tags: Mapping[str, str] | None = None,
    initial_backoff: float = DEFAULT_INITIAL_BACKOFF_SECONDS,
    max_backoff: float = DEFAULT_MAX_BACKOFF_SECONDS,
    summary_interval: float | None = DEFAULT_SUMMARY_INTERVAL_SECONDS,
) -> None:
    """Read every `interval` seconds and write what comes back, forever.

    `read` returns a value in physical units, or None to skip this tick —
    for a device that is warming up or has nothing new, which is not an
    error and should not be logged as one.

    **A `read` that raises is logged and retried, never fatal.** A vendor
    SDK that throws on a transient network blip, a device that reports busy,
    an expired session — none of these should end the process, because the
    failure that matters is the one nobody notices until a week of data is
    missing. Successive failures double the wait from `initial_backoff` up
    to `max_backoff`, and return to the nominal interval on the first
    success. An instrument with a known recovery time should be given it
    rather than left to the defaults.

    Runs until SIGINT (Ctrl+C) or SIGTERM (e.g. `docker stop`), at which
    point queued readings are flushed and the client closed.
    """
    loop = SensorLoop(summary_interval=summary_interval)

    logger.info(
        "writing readings",
        extra={
            "sensor_id": sensor_id,
            "measurement": measurement,
            "database": INFLUXDB_DATABASE,
            "interval_s": interval,
        },
    )

    backoff = initial_backoff

    while True:
        try:
            value = read()
        except Exception:
            logger.warning(
                "read failed",
                extra={"sensor_id": sensor_id, "retry_in_s": f"{backoff:.0f}"},
                exc_info=True,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
            continue

        backoff = initial_backoff
        if value is not None:
            point = build_point(
                value,
                sensor_id=sensor_id,
                measurement=measurement,
                unit=unit,
                field=field,
                tags=tags,
            )
            loop.record(point, sensor_id)
            logger.debug(
                "reading",
                extra={
                    "sensor_id": sensor_id,
                    "value": f"{value:.4g}",
                    "unit": unit,
                },
            )

        loop.summarise_if_due()
        time.sleep(interval)
