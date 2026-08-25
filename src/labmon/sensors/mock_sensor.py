"""Mock sensor that writes simulated readings to InfluxDB.

Examples:
    Room temperature (default: sensor "mock-sensor-1", 21°C setpoint, every 5s):
        $ uv run mock-sensor

    A second room sensor, sampled every second:
        $ uv run mock-sensor --sensor-id room-2 --setpoint 22 --interval 1 --unit "°C"

    A cryogenic zone sensor:
        $ uv run mock-sensor --sensor-id cryo-77k --setpoint 77 --noise 0.3 --unit K

    A vacuum gauge, spanning orders of magnitude (log-scale walk):
        $ uv run mock-sensor --sensor-id chamber-1 --measurement pressure \\
              --setpoint 1e-7 --noise 0.05 --log-scale --unit mbar

Requires INFLUXDB3_AUTH_TOKEN to be set (e.g. via .env / direnv).
"""

import argparse
import logging
import math
import random
import time
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import cast

from influxdb_client_3 import Point

from labmon import logs
from labmon.influx import influx_database
from labmon.sensors.loop import DEFAULT_SUMMARY_INTERVAL_SECONDS, SensorLoop

logger: logging.Logger = logging.getLogger(__name__)

# How many digits a reading carries when no absolute step is given.
# Significant digits rather than decimal places because a mock sensor may
# sit anywhere on the scale: the demo alone spans 4 K and 1.5e-7 mbar, and
# a fixed number of decimals reports one of those as zero.
DEFAULT_SIGNIFICANT_DIGITS = 6


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


def main() -> None:
    """CLI entry point (see module docstring for usage examples)."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    _ = parser.add_argument(
        "--sensor-id", default="mock-sensor-1", help="Tag identifying the sensor"
    )
    _ = parser.add_argument(
        "--interval", type=float, default=5.0, help="Seconds between readings"
    )
    _ = parser.add_argument(
        "--setpoint",
        type=float,
        default=21.0,
        help="Baseline reading the walk reverts toward",
    )
    _ = parser.add_argument(
        "--measurement",
        default="temperature",
        help="InfluxDB measurement (table) to write to",
    )
    _ = parser.add_argument(
        "--field", default="value", help="InfluxDB field name for the reading"
    )
    _ = parser.add_argument(
        "--noise",
        type=float,
        default=0.1,
        help="Std dev of Gaussian noise added each step "
        + "(in log10 units if --log-scale is set)",
    )
    _ = parser.add_argument(
        "--log-scale",
        action="store_true",
        help="Perform the walk in log10 space, for quantities spanning many "
        + "orders of magnitude (e.g. vacuum pressure). --setpoint stays in "
        + "linear units; --noise is interpreted in log10 units instead.",
    )
    _ = parser.add_argument(
        "--unit",
        default="",
        help="Unit of the reading (e.g. '°C', 'K', 'mbar'). Written as an "
        + "InfluxDB tag when set, and omitted entirely when not.",
    )
    _ = parser.add_argument(
        "--resolution",
        type=float,
        default=None,
        help="Absolute step the reading is rounded to, in its own units "
        + "(e.g. 0.001 for a thermometer resolving a millikelvin). "
        + "Without it, readings are rounded to --significant-digits, "
        + "which stays meaningful at any magnitude",
    )
    _ = parser.add_argument(
        "--significant-digits",
        type=int,
        default=DEFAULT_SIGNIFICANT_DIGITS,
        help="Digits a reading carries when --resolution is not given "
        + f"(default: {DEFAULT_SIGNIFICANT_DIGITS})",
    )
    _ = parser.add_argument(
        "--log-level",
        default="INFO",
        choices=logs.LEVEL_NAMES,
        type=str.upper,
        help="DEBUG shows every reading; INFO shows startup and the summary",
    )
    _ = parser.add_argument(
        "--summary-interval",
        type=float,
        default=DEFAULT_SUMMARY_INTERVAL_SECONDS,
        help="Seconds between 'still writing' summary lines; 0 turns them off",
    )
    args = parser.parse_args()

    logs.configure(logs.level_from_name(cast(str, args.log_level)))

    sensor_id = cast(str, args.sensor_id)
    interval = cast(float, args.interval)
    setpoint = cast(float, args.setpoint)
    measurement = cast(str, args.measurement)
    field = cast(str, args.field)
    noise = cast(float, args.noise)
    log_scale = cast(bool, args.log_scale)
    unit = cast(str, args.unit)
    resolution = cast(float | None, args.resolution)
    significant_digits = cast(int, args.significant_digits)
    # argparse cannot hand back None from a float flag, so zero is the off
    # switch — and "summarise every zero seconds" has no other meaning.
    summary_interval = cast(float, args.summary_interval) or None

    run(
        sensor_id=sensor_id,
        interval=interval,
        setpoint=setpoint,
        measurement=measurement,
        field=field,
        noise=noise,
        log_scale=log_scale,
        unit=unit,
        resolution=resolution,
        significant_digits=significant_digits,
        summary_interval=summary_interval,
    )


if __name__ == "__main__":
    main()  # pragma: no cover
