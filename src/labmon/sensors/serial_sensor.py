"""Read a board over serial and write calibrated readings to InfluxDB.

Ties together the two halves of real sensor acquisition: raw counts
arriving on a serial port (`labmon.sensors.serial_source`) and the
per-channel conversions that give them physical meaning
(`labmon.calibration`). Points reach InfluxDB through the same
queue-backed writer the mock sensor uses, so a network hiccup is
absorbed rather than losing readings.

Examples:
    An Arduino Due on a udev-pinned device path:
        $ uv run serial-sensor --port /dev/labmon-due \\
              --calibration calibration.toml

    A 10-bit board running at 5V:
        $ uv run serial-sensor --port /dev/ttyACM0 \\
              --calibration calibration.toml --resolution-bits 10 --vref 5.0

Requires INFLUXDB3_AUTH_TOKEN to be set (e.g. via .env / direnv).
"""

import argparse
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from influxdb_client_3 import Point

from labmon import logs
from labmon.calibration import (
    ADC_RESOLUTION_BITS,
    ADC_VREF_VOLTS,
    Calibration,
    load_calibration,
    raw_to_voltage,
    ureg,
)
from labmon.influx import INFLUXDB_DATABASE
from labmon.sensors.loop import SensorLoop
from labmon.sensors.serial_source import (
    DEFAULT_BAUDRATE,
    RawSource,
    SerialRawSource,
    open_serial_port,
)

logger: logging.Logger = logging.getLogger(__name__)

# The InfluxDB field every reading is written to. Unlike the mock
# sensor's --field, there's nothing to vary here: a channel's identity
# lives in its sensor_id tag, not in the field name.
FIELD_NAME = "value"

# The voltage a reading was converted from, written alongside the
# converted value unless the channel opts out (see Calibration.store_input).
# Named for its unit because the `unit` tag describes the converted value,
# not this.
INPUT_FIELD_NAME = "input_volts"

# Identifies which conversion produced a reading. A tag rather than a
# field so it can be filtered and grouped on; cardinality stays low
# because a calibration changes a handful of times over a sensor's life.
CALIBRATION_ID_TAG = "calibration_id"


def _log_calibrations(calibrations: dict[str, Calibration]) -> None:
    """Record which calibration each channel started with.

    The journal then says what was in force during a run, so a reading
    can be traced back even if the file has since been edited.
    """
    for channel, calibration in sorted(calibrations.items()):
        details = ", ".join(
            f"{key}={value!r}" for key, value in calibration.provenance.items()
        )
        logger.info(
            "calibration in force",
            extra={
                "channel": channel,
                "sensor_id": calibration.sensor_id,
                "calibration_id": calibration.calibration_id,
                "provenance": details or "-",
            },
        )


def run(
    source: RawSource,
    calibrations: dict[str, Calibration],
    resolution_bits: int = ADC_RESOLUTION_BITS,
    v_ref: float = ADC_VREF_VOLTS,
) -> None:
    """Write calibrated readings from `source` to InfluxDB until interrupted.

    Runs until a SIGINT (Ctrl+C) or SIGTERM (e.g. `docker stop`) is
    received, at which point the serial port and InfluxDB client are
    both closed cleanly.
    """
    # The port is closed before the writer, so nothing new arrives while
    # the queue drains.
    loop = SensorLoop(closes=source)

    logger.info(
        "writing calibrated readings",
        extra={
            "channels": ",".join(sorted(calibrations)) or "-",
            "database": INFLUXDB_DATABASE,
        },
    )
    _log_calibrations(calibrations)

    # A board may stream channels this host has no calibration for;
    # warn once each rather than on every reading forever.
    warned_channels: set[str] = set()

    while True:
        reading = source.read()
        if reading is None:
            continue

        calibration = calibrations.get(reading.channel)
        if calibration is None:
            if reading.channel not in warned_channels:
                warned_channels.add(reading.channel)
                logger.warning(
                    "no calibration for channel; ignoring its readings",
                    extra={"channel": reading.channel},
                )
            continue

        voltage = raw_to_voltage(reading.raw_count, resolution_bits, v_ref)
        value = calibration.conversion.apply(voltage)

        # The board has no clock, so the host stamps the reading on
        # arrival; serial transit is negligible next to sample rates here.
        point = (
            Point(calibration.measurement)
            .tag("sensor_id", calibration.sensor_id)
            .tag("unit", calibration.unit)
            .tag(CALIBRATION_ID_TAG, calibration.calibration_id)
            .field(FIELD_NAME, value.magnitude)
            .time(datetime.now(UTC), write_precision="ms")
        )
        if calibration.store_input:
            # Keeping the conversion's input makes a wrong calibration
            # correctable after the fact; without it, readings already
            # written can't be recomputed.
            point = point.field(INPUT_FIELD_NAME, voltage.to(ureg.volt).magnitude)
        loop.record(point, calibration.sensor_id)
        logger.debug(
            "reading",
            extra={
                "sensor_id": calibration.sensor_id,
                "value": f"{value.magnitude:.4g}",
                "unit": calibration.unit,
            },
        )
        loop.summarise_if_due()


def main() -> None:
    """CLI entry point (see module docstring for usage examples)."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    _ = parser.add_argument(
        "--port", required=True, help="Serial device to read (e.g. /dev/labmon-due)"
    )
    _ = parser.add_argument(
        "--calibration",
        required=True,
        help="Path to the TOML file mapping channels to conversions",
    )
    _ = parser.add_argument(
        "--baudrate",
        type=int,
        default=DEFAULT_BAUDRATE,
        help="Serial baud rate. Ignored by a board using native USB (the "
        + "Arduino Due's native port included), which always runs at full "
        + "USB speed, but pyserial still requires a value.",
    )
    _ = parser.add_argument(
        "--resolution-bits",
        type=int,
        default=ADC_RESOLUTION_BITS,
        help="ADC resolution in bits (default suits a 12-bit part)",
    )
    _ = parser.add_argument(
        "--vref",
        type=float,
        default=ADC_VREF_VOLTS,
        help="ADC reference voltage (default suits a 3.3V part)",
    )
    _ = parser.add_argument(
        "--log-level",
        default="INFO",
        help="DEBUG shows every reading; INFO shows startup and the summary",
    )
    args = parser.parse_args()

    port = cast(str, args.port)
    calibration_path = cast(str, args.calibration)
    baudrate = cast(int, args.baudrate)
    resolution_bits = cast(int, args.resolution_bits)
    v_ref = cast(float, args.vref)

    logs.configure(getattr(logging, cast(str, args.log_level).upper(), logging.INFO))

    # Load the calibration before opening the port: a bad config should
    # fail immediately rather than after touching the hardware.
    calibrations = load_calibration(Path(calibration_path))
    source = SerialRawSource(open_serial_port(port, baudrate=baudrate))

    run(
        source=source,
        calibrations=calibrations,
        resolution_bits=resolution_bits,
        v_ref=v_ref,
    )


if __name__ == "__main__":
    main()  # pragma: no cover
