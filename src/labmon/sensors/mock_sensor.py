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
from decimal import ROUND_HALF_EVEN, Decimal

from influxdb_client_3 import Point

from labmon.influx import influx_database
from labmon.sensors import constants
from labmon.sensors.loop import DEFAULT_SUMMARY_INTERVAL_SECONDS, SensorLoop

logger: logging.Logger = logging.getLogger(__name__)

# How many digits a reading carries when no absolute step is given.
# Significant digits rather than decimal places because a mock sensor may
# sit anywhere on the scale: the demo alone spans 4 K and 1.5e-7 mbar, and
# a fixed number of decimals reports one of those as zero.
DEFAULT_SIGNIFICANT_DIGITS = constants.DEFAULT_SIGNIFICANT_DIGITS


def quantise(
    value: float,
    resolution: float | None = None,
    significant_digits: int = DEFAULT_SIGNIFICANT_DIGITS,
) -> float:
    """Round a reading to the precision the simulated instrument has.

    A walk in floating point produces sixteen digits, and writing them
    claims a precision no instrument has — someone reading the exported
    column cannot tell simulated jitter from a real millikelvin.

    `resolution` is an absolute step in the reading's own units, which is
    how an instrument with a fixed least-significant digit is specified
    (a thermometer resolving 0.001 K). Without one, the value is rounded
    to `significant_digits` instead, which stays meaningful at any
    magnitude — the safe default, since an absolute step large enough for
    a thermometer reports a vacuum gauge as zero.

    Stepping is done in Decimal rather than as `round(value / step) *
    step`: the arithmetic form puts back the noise it is removing, giving
    76.85000000000001 where the decimal form gives 76.85.
    """
    if not math.isfinite(value):
        # `polling` refuses a non-finite value before it becomes a field;
        # rounding should not be the thing that raises first.
        return value
    if resolution is not None:
        step = Decimal(str(resolution))
        return float(
            (Decimal(repr(value)) / step).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
            * step
        )
    return float(f"{value:.{significant_digits}g}")


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
