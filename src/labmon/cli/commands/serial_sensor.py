"""`labmon serial-sensor` — read a board over a serial port."""

from pathlib import Path
from typing import Annotated

import typer

from labmon.adc import ADC_RESOLUTION_BITS, ADC_VREF_VOLTS
from labmon.cli.options import LogLevelOption, SummaryInterval
from labmon.cli.runtime import configure
from labmon.sensors.constants import (
    DEFAULT_BAUDRATE,
    DEFAULT_SUMMARY_INTERVAL_SECONDS,
)

HELP = (
    "Read raw counts from a board over serial, convert them with a"
    + " calibration file, and write the results to InfluxDB."
    + "\n\n"
    + "The calibration is loaded before the port is opened, so a bad config"
    + " fails immediately rather than after touching the hardware."
    + "\n\n"
    + "[bold]Examples[/bold]\n\n"
    + "    labmon serial-sensor --port /dev/ttyACM0 --calibration cal.toml\n"
    + "    labmon serial-sensor --port /dev/labmon-board --calibration cal.toml"
    + " --resolution-bits 10 --vref 5.0"
)


def serial_sensor(
    port: Annotated[
        str,
        typer.Option("--port", help="Serial device to read (e.g. /dev/ttyACM0)"),
    ],
    calibration: Annotated[
        Path,
        typer.Option(
            "--calibration",
            help="TOML file mapping channels to conversions",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ],
    baudrate: Annotated[
        int,
        typer.Option(
            "--baudrate",
            help="Serial baud rate. Ignored by a board using native USB, "
            + "which always runs at full USB speed, but pyserial still "
            + "requires a value",
        ),
    ] = DEFAULT_BAUDRATE,
    resolution_bits: Annotated[
        int,
        typer.Option(
            "--resolution-bits",
            help="ADC resolution in bits (default suits a 12-bit part)",
        ),
    ] = ADC_RESOLUTION_BITS,
    vref: Annotated[
        float,
        typer.Option(
            "--vref", help="ADC reference voltage (default suits a 3.3V part)"
        ),
    ] = ADC_VREF_VOLTS,
    summary_interval: SummaryInterval = DEFAULT_SUMMARY_INTERVAL_SECONDS,
    log_level: LogLevelOption = "INFO",  # pyright: ignore[reportArgumentType]
) -> None:
    """Read calibrated readings from a board on a serial port."""
    from labmon.calibration import load_calibration
    from labmon.sensors import serial_sensor as sensor
    from labmon.sensors.serial_source import SerialRawSource, open_serial_port

    configure(log_level)
    # Loaded before the port is opened: a bad config should fail
    # immediately rather than after touching the hardware.
    calibrations = load_calibration(calibration)
    source = SerialRawSource(open_serial_port(port, baudrate=baudrate))
    # Called through the module rather than a bound name so a test
    # can replace it.
    sensor.run(
        source=source,
        calibrations=calibrations,
        resolution_bits=resolution_bits,
        v_ref=vref,
        summary_interval=summary_interval or None,
    )
