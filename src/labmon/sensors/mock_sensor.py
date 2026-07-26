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
import math
import random
import signal
import sys
import time
from datetime import UTC, datetime
from types import FrameType
from typing import cast

from influxdb_client_3 import Point

from labmon.influx import INFLUXDB_DATABASE, get_client
from labmon.writer import PointWriter


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
) -> None:
    """Write simulated readings for `sensor_id` to InfluxDB until interrupted.

    Runs until a SIGINT (Ctrl+C) or SIGTERM (e.g. `docker stop`) is
    received, at which point the InfluxDB client is closed cleanly.
    """
    writer: PointWriter[Point] = PointWriter[Point](get_client())
    walk = RandomWalk(setpoint=setpoint, noise=noise, log_scale=log_scale)

    def shutdown(_signum: int, _frame: FrameType | None) -> None:
        writer.close()
        sys.exit(0)

    _ = signal.signal(signal.SIGINT, shutdown)
    _ = signal.signal(signal.SIGTERM, shutdown)

    unit_suffix = f" {unit}" if unit else ""
    print(
        f"Writing mock '{measurement}' readings for '{sensor_id}' to "
        + f"{INFLUXDB_DATABASE} every {interval}s"
    )
    while True:
        reading_time = datetime.now(UTC)
        reading = walk.next()
        point = (
            Point(measurement)
            .tag("sensor_id", sensor_id)
            .field(field, reading)
            .time(reading_time, write_precision="ms")
        )
        writer.write(point)
        print(f"{sensor_id}: {reading:.4g}{unit_suffix}")
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
        help="Cosmetic unit suffix for console output only (e.g. '°C', 'K', 'mbar')",
    )
    args = parser.parse_args()

    sensor_id = cast(str, args.sensor_id)
    interval = cast(float, args.interval)
    setpoint = cast(float, args.setpoint)
    measurement = cast(str, args.measurement)
    field = cast(str, args.field)
    noise = cast(float, args.noise)
    log_scale = cast(bool, args.log_scale)
    unit = cast(str, args.unit)

    run(
        sensor_id=sensor_id,
        interval=interval,
        setpoint=setpoint,
        measurement=measurement,
        field=field,
        noise=noise,
        log_scale=log_scale,
        unit=unit,
    )


if __name__ == "__main__":
    main()  # pragma: no cover
