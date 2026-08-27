"""Simulating a sensor, for trying the stack without hardware.

A mean-reverting random walk around a setpoint, optionally in log10
space for a quantity spanning decades. `run()` is the loop; the command
line that drives it is `labmon.cli.commands.mock_sensor`.

This module is also the template somebody copies to write a real sensor
— see `docs/custom-sensor.md`.
"""

import logging
import math
import random
import time
from datetime import UTC, datetime

from influxdb_client_3 import Point

from labmon.influx import influx_database
from labmon.quantise import DEFAULT_SIGNIFICANT_DIGITS, quantise
from labmon.sensors.loop import DEFAULT_SUMMARY_INTERVAL_SECONDS, SensorLoop

logger: logging.Logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_SIGNIFICANT_DIGITS", "quantise", "run"]


class RandomWalk:
    """A mean-reverting random walk used to simulate a sensor reading.

    Each call to next() nudges the current value back towards `setpoint`
    (so it doesn't drift away forever) and adds Gaussian noise on top, to
    mimic real sensor jitter.

    When log_scale is True, the walk is performed in log10 space: setpoint,
    internal state, and noise are all in log10(reading) units, so jitter
    and mean-reversion scale multiplicatively with magnitude rather than by
    a fixed absolute amount. This suits quantities spanning many orders of
    magnitude (e.g. vacuum pressure) and never produces a non-positive
    reading. next() always returns the reading in linear, real-world units.
    """

    def __init__(
        self, setpoint: float, noise: float = 0.1, log_scale: bool = False
    ) -> None:
        self.log_scale: bool = log_scale
        self.noise: float = noise
        self.setpoint: float = math.log10(setpoint) if log_scale else setpoint
        self.value: float = self.setpoint

    def next(self) -> float:
        """Advance the walk by one step and return the new reading."""
        pull_to_setpoint = (self.setpoint - self.value) * 0.02
        self.value += pull_to_setpoint + random.normalvariate(0, self.noise)
        return 10**self.value if self.log_scale else self.value


def run(
    sensor_id: str,
    interval: float,
    setpoint: float,
    measurement: str = "temperature",
    field: str = "value",
    noise: float = 0.1,
    log_scale: bool = False,
    unit: str = "",
    resolution: float | None = None,
    significant_digits: int = DEFAULT_SIGNIFICANT_DIGITS,
    summary_interval: float | None = DEFAULT_SUMMARY_INTERVAL_SECONDS,
) -> None:
    """Write simulated readings for `sensor_id` to InfluxDB until interrupted.

    Runs until a SIGINT (Ctrl+C) or SIGTERM (e.g. `docker stop`) is
    received, at which point the InfluxDB client is closed cleanly.
    """
    # The summary is on now that per-reading lines are DEBUG. Without it a
    # sensor at the default level would say nothing at all after startup,
    # and silence is indistinguishable from a wedged process.
    loop = SensorLoop(summary_interval=summary_interval, sensors=(sensor_id,))
    walk = RandomWalk(setpoint=setpoint, noise=noise, log_scale=log_scale)

    logger.info(
        "writing simulated readings",
        extra={
            "sensor_id": sensor_id,
            "measurement": measurement,
            "database": influx_database(),
            "interval_s": interval,
        },
    )
    while True:
        reading_time = datetime.now(UTC)
        # Rounded here rather than inside the walk: quantising the walk's
        # own state would change how it reverts to the setpoint, and a
        # step coarser than the noise would freeze it outright. The gate
        # then sees the value that will actually be recorded.
        reading = quantise(
            walk.next(),
            resolution=resolution,
            significant_digits=significant_digits,
        )
        # The walk cannot currently produce one, but this file is also the
        # template a user copies and edits, and the guard belongs wherever
        # a value becomes a field. Guarding the write rather than skipping
        # the rest of the iteration, so the pacing stays in one place and a
        # bad reading cannot turn the loop into a spin.
        if loop.admits(reading, sensor_id=sensor_id):
            point = Point(measurement).tag("sensor_id", sensor_id)
            if unit:
                point = point.tag("unit", unit)
            point = point.field(field, reading).time(reading_time, write_precision="ms")
            loop.record(point, sensor_id)
            logger.debug(
                "reading",
                extra={"sensor_id": sensor_id, "value": f"{reading:.4g}", "unit": unit},
            )
        loop.summarise_if_due()
        time.sleep(interval)
