"""Mock temperature sensor that writes simulated readings to InfluxDB.

Examples:
    Run with defaults (sensor "mock-temp-1", 21°C setpoint, every 5s):
        $ uv run mock-temperature-sensor

    Simulate a fridge, sampled every second:
        $ uv run mock-temperature-sensor --sensor-id fridge-2 --setpoint 4 --interval 1

Requires INFLUXDB3_AUTH_TOKEN to be set (e.g. via .env / direnv).
"""

import argparse
import random
import signal
import sys
import time
from datetime import UTC, datetime

from influxdb_client_3 import Point

from labmon.influx import INFLUXDB_DATABASE, get_client
from labmon.writer import PointWriter


class TemperatureWalk:
    """A mean-reverting random walk used to simulate a temperature sensor.

    Each call to next() nudges the current value back towards `setpoint`
    (so it doesn't drift away forever) and adds Gaussian noise on top, to
    mimic real sensor jitter.
    """

    def __init__(self, setpoint: float, noise: float = 0.1):
        self.setpoint = setpoint
        self.noise = noise
        self.value = setpoint

    def next(self) -> float:
        """Advance the walk by one step and return the new reading."""
        pull_to_setpoint = (self.setpoint - self.value) * 0.02
        self.value += pull_to_setpoint + random.normalvariate(0, self.noise)
        return round(self.value, 2)


def run(sensor_id: str, interval: float, setpoint: float) -> None:
    """Write simulated readings for `sensor_id` to InfluxDB until interrupted.

    Runs until a SIGINT (Ctrl+C) or SIGTERM (e.g. `docker stop`) is
    received, at which point the InfluxDB client is closed cleanly.
    """
    writer = PointWriter(get_client())
    walk = TemperatureWalk(setpoint=setpoint)

    def shutdown(signum, frame):
        writer.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(
        f"Writing mock readings for '{sensor_id}' to {INFLUXDB_DATABASE} every {interval}s"
    )
    while True:
        reading_time = datetime.now(UTC)
        temperature = walk.next()
        point = (
            Point("temperature")
            .tag("sensor_id", sensor_id)
            .field("value", temperature)
            .time(reading_time, write_precision="ms")
        )
        writer.write(point)
        print(f"{sensor_id}: {temperature}°C")
        time.sleep(interval)


def main():
    """CLI entry point (see module docstring for usage examples)."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--sensor-id", default="mock-temp-1", help="Tag identifying the sensor"
    )
    parser.add_argument(
        "--interval", type=float, default=5.0, help="Seconds between readings"
    )
    parser.add_argument(
        "--setpoint", type=float, default=21.0, help="Baseline temperature in °C"
    )
    args = parser.parse_args()

    run(sensor_id=args.sensor_id, interval=args.interval, setpoint=args.setpoint)


if __name__ == "__main__":
    main()
