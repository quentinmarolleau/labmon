"""`labmon mock-sensor` — write simulated readings."""

from typing import Annotated

import typer

from labmon.cli.options import LogLevelOption, SummaryInterval
from labmon.cli.runtime import configure
from labmon.sensors import mock_sensor as sensor
from labmon.sensors.loop import DEFAULT_SUMMARY_INTERVAL_SECONDS
from labmon.sensors.mock_sensor import DEFAULT_SIGNIFICANT_DIGITS

HELP = (
    "Write simulated readings to InfluxDB, for trying the stack without"
    + " hardware."
    + "\n\n"
    + "The reading is a mean-reverting random walk around"
    + " [bold]--setpoint[/bold]. With [bold]--log-scale[/bold] the walk runs"
    + " in log10 space, so jitter scales multiplicatively — which suits a"
    + " quantity spanning decades, such as vacuum pressure."
    + "\n\n"
    + "[bold]Examples[/bold]\n\n"
    + "    labmon mock-sensor\n"
    + "    labmon mock-sensor --sensor-id room-2 --setpoint 22 --unit °C\n"
    + "    labmon mock-sensor --sensor-id cryo-77k --setpoint 77 --unit K"
    + " --resolution 0.001\n"
    + "    labmon mock-sensor --sensor-id chamber-1 --measurement pressure"
    + " --setpoint 1e-7 --log-scale --unit mbar"
)


def mock_sensor(
    sensor_id: Annotated[
        str, typer.Option("--sensor-id", help="Tag identifying the sensor")
    ] = "mock-sensor-1",
    interval: Annotated[
        float, typer.Option("--interval", help="Seconds between readings")
    ] = 5.0,
    setpoint: Annotated[
        float,
        typer.Option("--setpoint", help="Baseline reading the walk reverts toward"),
    ] = 21.0,
    measurement: Annotated[
        str,
        typer.Option("--measurement", help="InfluxDB measurement (table) to write to"),
    ] = "temperature",
    field: Annotated[
        str, typer.Option("--field", help="InfluxDB field name for the reading")
    ] = "value",
    noise: Annotated[
        float,
        typer.Option(
            "--noise",
            help="Std dev of Gaussian noise added each step "
            + "(in log10 units if --log-scale is set)",
        ),
    ] = 0.1,
    log_scale: Annotated[
        bool,
        typer.Option(
            "--log-scale",
            help="Walk in log10 space, for a quantity spanning decades. "
            + "--setpoint stays in linear units; --noise becomes log10",
        ),
    ] = False,
    unit: Annotated[
        str,
        typer.Option(
            "--unit",
            help="Unit of the reading (e.g. '°C', 'K', 'mbar'). Written as an "
            + "InfluxDB tag when set, and omitted entirely when not",
        ),
    ] = "",
    resolution: Annotated[
        float | None,
        typer.Option(
            "--resolution",
            help="Absolute step the reading is rounded to, in its own units "
            + "(e.g. 0.001 for a thermometer resolving a millikelvin). Without "
            + "it, readings are rounded to --significant-digits, which stays "
            + "meaningful at any magnitude",
            show_default=False,
        ),
    ] = None,
    significant_digits: Annotated[
        int,
        typer.Option(
            "--significant-digits",
            help="Digits a reading carries when --resolution is not given",
        ),
    ] = DEFAULT_SIGNIFICANT_DIGITS,
    summary_interval: SummaryInterval = DEFAULT_SUMMARY_INTERVAL_SECONDS,
    log_level: LogLevelOption = "INFO",  # pyright: ignore[reportArgumentType]
) -> None:
    """Write simulated readings, for trying the stack without hardware."""
    configure(log_level)
    # Called through the module rather than a bound name so a test
    # can replace it, and so a reload sees the new function.
    sensor.run(
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
        # Typer cannot hand back None from a float option, so zero is the
        # off switch — "summarise every zero seconds" has no other meaning.
        summary_interval=summary_interval or None,
    )
