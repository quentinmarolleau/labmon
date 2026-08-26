"""Rendering readings as a table a person reads in a terminal.

Separate from the file writers because the goals conflict: a file wants
every column and full precision so nothing is lost, while a terminal
wants the few columns that carry meaning, aligned, in a width that fits.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from labmon.cli.age import Freshness, describe, freshness

if TYPE_CHECKING:
    import pyarrow as pa

# Columns worth a terminal's width, in reading order. `calibration_id` and
# `input_volts` are deliberately absent: they are provenance, they are in
# every exported file, and a hash column pushes the value off the screen.
PREFERRED_COLUMNS: tuple[str, ...] = (
    "time",
    "sensor_id",
    "measurement",
    "value",
    "unit",
)

# Rows shown when --limit is not given. Enough to see a trend, few enough
# that a stray `labmon query` over a week does not fill the scrollback.
DEFAULT_LIMIT = 20


def _is_all_null(column: "pa.ChunkedArray") -> bool:
    return all(value is None for value in column.to_pylist())


def visible_columns(table: "pa.Table") -> list[str]:
    """Which of the preferred columns this result actually has content in.

    A measurement written by something other than labmon may carry no
    unit at all, and a column of nothing but blanks is worse than absent:
    it takes width and invites the reader to wonder what is missing.
    """
    return [
        name
        for name in PREFERRED_COLUMNS
        if name in table.column_names and not _is_all_null(table.column(name))
    ]


def _cell(name: str, value: object) -> str:
    if value is None:
        return ""
    if name == "time" and isinstance(value, datetime):
        # Seconds and milliseconds, without the timezone suffix: every
        # timestamp in a result carries the same one, so repeating it on
        # every row costs width and says nothing. Dropping the zone and
        # asking for milliseconds explicitly, rather than slicing to a
        # fixed width: `str` omits the fractional part when it is zero,
        # so the slice cut into the offset instead of the digits — and
        # a whole second is what a sensor on a 1 Hz grid reports.
        return value.replace(tzinfo=None).isoformat(sep=" ", timespec="milliseconds")
    if name == "value" and isinstance(value, float):
        # `repr` rather than a fixed precision: sensors already round to
        # the resolution they claim, so this shows exactly what was
        # stored rather than inventing or hiding digits.
        return repr(value)
    return str(value)


def render(table: "pa.Table", limit: int = DEFAULT_LIMIT) -> str:
    """Format `table` as an aligned table, most recent rows last.

    A limit of 0 means every row. When rows are dropped, the footer says
    so — a silently truncated table is one somebody draws a conclusion
    from.
    """
    total = table.num_rows
    if total == 0:
        return "no readings matched"

    shown = table if limit <= 0 or total <= limit else table.slice(total - limit, limit)
    names = visible_columns(shown)
    columns = {name: shown.column(name).to_pylist() for name in names}

    rows = [
        [_cell(name, columns[name][index]) for name in names]
        for index in range(shown.num_rows)
    ]
    widths = [
        max(len(name), *(len(row[position]) for row in rows))
        for position, name in enumerate(names)
    ]

    lines = [
        "  ".join(name.ljust(width) for name, width in zip(names, widths, strict=True)),
        "  ".join("-" * width for width in widths),
    ]
    lines.extend(
        "  ".join(
            cell.ljust(width) for cell, width in zip(row, widths, strict=True)
        ).rstrip()
        for row in rows
    )

    if shown.num_rows < total:
        lines.append("")
        lines.append(f"showing the last {shown.num_rows} of {total} readings")
    else:
        lines.append("")
        lines.append(f"{total} reading{'s' if total != 1 else ''}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# The latest reading from each sensor
# --------------------------------------------------------------------------

LATEST_COLUMNS: tuple[str, ...] = ("sensor_id", "measurement", "value", "unit", "age")

# SGR codes rather than a styling library: this is the only colour the
# CLI emits, and reaching for one would pull an import into a path the
# startup work deliberately keeps clear.
_STYLES: dict[Freshness, str] = {
    Freshness.FRESH: "",
    Freshness.AGEING: "\x1b[33m",
    Freshness.STALE: "\x1b[31m",
}
_RESET = "\x1b[0m"


def render_latest(table: "pa.Table", now: datetime, *, colour: bool = False) -> str:
    """One row per sensor, oldest last, with how long ago it reported.

    Sorted by age rather than by name, ascending, so the sensor that has
    stopped reporting is the last line — which is where an eye lands on
    a short list, and it is the row this view exists to surface.

    Colour is applied after padding, never before. An escape code counts
    toward `len`, so a cell coloured first pads to fewer visible
    characters than its neighbours and silently shortens its column.
    """
    total = table.num_rows
    if total == 0:
        return "no readings matched"

    columns = {name: table.column(name).to_pylist() for name in table.column_names}
    ages = [now - _as_datetime(stamp) for stamp in columns["time"]]
    order = sorted(range(total), key=lambda index: ages[index])

    present = [name for name in LATEST_COLUMNS if name == "age" or name in columns]
    rows: list[list[str]] = []
    styles: list[str] = []
    for index in order:
        cells = [
            describe(ages[index])
            if name == "age"
            else _cell(name, columns[name][index])
            for name in present
        ]
        rows.append(cells)
        styles.append(_STYLES[freshness(ages[index])] if colour else "")

    widths = [
        max(len(name), *(len(row[position]) for row in rows))
        for position, name in enumerate(present)
    ]

    lines = [
        "  ".join(
            name.ljust(width) for name, width in zip(present, widths, strict=True)
        ),
        "  ".join("-" * width for width in widths),
    ]
    for row, style in zip(rows, styles, strict=True):
        padded = "  ".join(
            cell.ljust(width) for cell, width in zip(row, widths, strict=True)
        ).rstrip()
        lines.append(f"{style}{padded}{_RESET}" if style else padded)

    lines.append("")
    lines.append(f"{total} sensor{'s' if total != 1 else ''}")
    return "\n".join(lines)


def _as_datetime(stamp: object) -> datetime:
    """A timestamp column entry as an aware datetime.

    pyarrow hands back a naive value for a column with no zone, and the
    latest query stamps in UTC, so an absent zone means UTC rather than
    local time.
    """
    moment = cast(datetime, stamp)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
