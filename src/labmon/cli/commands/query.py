"""`labmon query` — print readings to stdout for a person to read."""

from typing import Annotated

import typer

from labmon.cli import selection
from labmon.cli.options import LogLevelOption, Measurements, SensorIds, Since, Until
from labmon.cli.render import DEFAULT_LIMIT, render
from labmon.cli.runtime import configure

HELP = (
    "Print recorded readings as a table."
    + "\n\n"
    + "The most recent readings are shown last, the way a log reads. Only"
    + " the columns that carry meaning are shown, and one that is empty for"
    + " every row is left out. Use [bold]labmon export[/bold] to write a"
    + " file, or to pipe a real format into another tool."
    + "\n\n"
    + "[bold]Examples[/bold]\n\n"
    + "    labmon query --measurement temperature --since 5m\n"
    + "    labmon query --sensor-id cryo-77k --since 24h --limit 50\n"
    + "    labmon query --since 1h --limit 0"
)

Limit = Annotated[
    int,
    typer.Option(
        "--limit",
        help="Show at most this many of the most recent readings; 0 shows every one",
        metavar="N",
    ),
]


def query(
    measurement: Measurements = None,
    sensor_id: SensorIds = None,
    since: Since = "1h",
    until: Until = None,
    limit: Limit = DEFAULT_LIMIT,
    log_level: LogLevelOption = "INFO",  # pyright: ignore[reportArgumentType]
) -> None:
    """Print recorded readings as a table."""
    configure(log_level)
    table, _window = selection.read(measurement, sensor_id, since, until)
    print(render(table, limit=limit))
