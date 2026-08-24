"""Option types shared between commands.

`query` and `export` ask the database the same question and differ only
in what they do with the answer. Their selection flags are declared once
here, as annotated aliases, and named in both signatures — so the two
cannot drift into accepting different spellings of the same idea, and
the help text is written in one place.
"""

import enum
from typing import Annotated

import typer

from labmon.export.writers import SUFFIXES


class Format(str, enum.Enum):
    """Output formats, as an enum so the choices complete and validate."""

    csv = "csv"
    parquet = "parquet"
    feather = "feather"
    netcdf = "netcdf"


class LogLevel(str, enum.Enum):
    debug = "DEBUG"
    info = "INFO"
    warning = "WARNING"
    error = "ERROR"
    critical = "CRITICAL"


Measurements = Annotated[
    list[str] | None,
    typer.Option(
        "--measurement",
        help="Measurement (table) to read; repeatable. Default: every one",
        metavar="NAME",
        show_default=False,
    ),
]

SensorIds = Annotated[
    list[str] | None,
    typer.Option(
        "--sensor-id",
        help="Restrict to this sensor; repeatable. Default: every sensor",
        metavar="ID",
        show_default=False,
    ),
]

Since = Annotated[
    str,
    typer.Option(
        "--since",
        help="Window start: an ISO 8601 timestamp (2026-08-01, "
        + "2026-08-01T14:30:00+02:00) or a duration ago (24h, 90m, 7d). "
        + "A timestamp with no offset is read as UTC",
        metavar="WHEN",
    ),
]

Until = Annotated[
    str | None,
    typer.Option(
        "--until",
        help="Window end, same spellings as --since. Default: now",
        metavar="WHEN",
        show_default=False,
    ),
]

LogLevelOption = Annotated[
    LogLevel,
    typer.Option(
        "--log-level",
        help="DEBUG shows more detail",
        case_sensitive=False,
    ),
]

SummaryInterval = Annotated[
    float,
    typer.Option(
        "--summary-interval",
        help="Seconds between 'still writing' summary lines; 0 turns them off",
    ),
]


def format_help() -> str:
    """The --format help, listing what each extension maps to."""
    pairs = ", ".join(f"{name} ({suffix})" for name, suffix in SUFFIXES.items())
    return (
        f"Output format: {pairs}."
        + " Default: inferred from --output, else csv."
        + " netcdf needs the 'netcdf' extra"
    )
