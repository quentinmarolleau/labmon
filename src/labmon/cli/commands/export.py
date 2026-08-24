"""`labmon export` — write recorded readings to a file."""

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from labmon.cli import selection
from labmon.cli.options import (
    Format,
    LogLevelOption,
    Measurements,
    SensorIds,
    Since,
    Until,
    format_help,
)
from labmon.cli.runtime import configure
from labmon.export.formats import SUFFIXES, ExportError

if TYPE_CHECKING:
    import pyarrow as pa

    from labmon.export.window import Window

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_STEM = "labmon-export"

HELP = (
    "Export recorded readings to a file a notebook can open."
    + "\n\n"
    + "Output is long format — one row per reading — because that is what"
    + " the database holds and it survives sensors running at different"
    + " rates. A wide table is one pivot away. The unit rides along on"
    + " every row, in every format."
    + "\n\n"
    + "Use [bold]labmon query[/bold] to look at readings in the terminal"
    + " instead."
    + "\n\n"
    + "[bold]Examples[/bold]\n\n"
    + "    labmon export --sensor-id cryo-77k --since 2026-08-01\n"
    + "    labmon export --measurement temperature --since 24h --format parquet\n"
    + "    labmon export --since 5m --format feather -o test\n"
    + "    labmon export --since 7d --split-per-sensor -o run.feather\n"
    + "    labmon export --since 1h -o - | head"
)

FormatOption = Annotated[
    Format | None,
    typer.Option("--format", help=format_help(), show_default=False),
]

Output = Annotated[
    str | None,
    typer.Option(
        "-o",
        "--output",
        help="File to write, or '-' for stdout. The format's extension is "
        + "appended when the path does not already end in it",
        metavar="PATH",
        show_default=False,
    ),
]

SplitPerSensor = Annotated[
    bool,
    typer.Option(
        "--split-per-sensor",
        help="Write <name>_<sensor_id>.<ext> per sensor instead of one file",
    ),
]


def infer_format(output: str | None, requested: Format | None) -> str:
    """Pick the format, preferring an explicit flag over the filename."""
    if requested is not None:
        return requested.value
    if output and output != "-":
        suffix = Path(output).suffix.lower()
        for name, extension in SUFFIXES.items():
            if suffix == extension:
                return name
    return Format.csv.value


def with_suffix(output: str, fmt: str) -> Path:
    """Give `output` the chosen format's extension unless it has it already.

    `-o test --format feather` writes `test.feather`, so a directory does
    not fill with extensionless files that nothing can identify. Only the
    correct extension counts as already present: `-o run.2026-08-24`
    gains one, because `.24` names nothing.

    A path ending in a *different* format's extension is left as typed.
    `-o run.csv --format parquet` contradicts itself, and honouring the
    explicit flag while renaming the explicit filename would be the
    surprising half of that.
    """
    path = Path(output)
    if path.suffix.lower() == SUFFIXES[fmt]:
        return path
    if path.suffix.lower() in set(SUFFIXES.values()):
        logger.warning(
            "writing one format under another format's extension",
            extra={"path": str(path), "format": fmt},
        )
        return path
    return path.with_name(path.name + SUFFIXES[fmt])


def split_tables(table: "pa.Table") -> "list[tuple[str, pa.Table]]":
    """One (sensor_id, rows) pair per sensor, in a stable order."""
    import pyarrow as pa
    import pyarrow.compute as pc

    column = table.column("sensor_id")
    values = column.to_pylist()
    sensors = sorted({"unnamed" if value is None else str(value) for value in values})
    parts: list[tuple[str, pa.Table]] = []
    for sensor in sensors:
        if sensor == "unnamed":
            mask = pc.is_null(column)
        else:
            mask = pc.equal(column.cast(pa.string()), pa.scalar(sensor))
        parts.append((sensor, table.filter(mask)))
    return parts


def _write_split(table: "pa.Table", target: Path, fmt: str, window: "Window") -> None:
    from labmon.export.table import attach_metadata
    from labmon.export.writers import safe_filename_part, write

    suffix = target.suffix or SUFFIXES[fmt]
    stem = target.stem if target.suffix else target.name
    for sensor, rows in split_tables(table):
        part = target.with_name(f"{stem}_{safe_filename_part(sensor)}{suffix}")
        write(attach_metadata(rows, window), part, fmt)
        logger.info("wrote file", extra={"path": str(part), "rows": rows.num_rows})


def export(
    measurement: Measurements = None,
    sensor_id: SensorIds = None,
    since: Since = "1h",
    until: Until = None,
    output: Output = None,
    format: FormatOption = None,
    split_per_sensor: SplitPerSensor = False,
    log_level: LogLevelOption = "INFO",  # pyright: ignore[reportArgumentType]
) -> None:
    """Export recorded readings to a file a notebook can open."""
    from labmon.export.table import attach_metadata
    from labmon.export.writers import write, write_stdout

    configure(log_level)
    fmt = infer_format(output, format)

    if output == "-" and split_per_sensor:
        raise ExportError(
            "--split-per-sensor writes one file per sensor, so it cannot"
            + " also write to stdout; give --output a filename"
        )

    readings, window = selection.read(measurement, sensor_id, since, until)
    table = attach_metadata(readings, window)
    logger.info("exported readings", extra={"rows": table.num_rows, "format": fmt})
    if table.num_rows == 0:
        logger.warning(
            "no readings matched",
            extra={
                "since": window.since.isoformat(),
                "until": window.until.isoformat(),
            },
        )

    if output == "-":
        write_stdout(table, fmt, sys.stdout.buffer)
        return

    target = with_suffix(output, fmt) if output else Path(DEFAULT_STEM + SUFFIXES[fmt])
    if split_per_sensor:
        _write_split(table, target, fmt, window)
        return

    write(table, target, fmt)
    logger.info("wrote file", extra={"path": str(target), "rows": table.num_rows})
