"""Rendering readings as a table a person reads in a terminal.

Separate from the file writers because the goals conflict: a file wants
every column and full precision so nothing is lost, while a terminal
wants the few columns that carry meaning, aligned, in a width that fits.
"""

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


def _is_all_null(column: pa.ChunkedArray) -> bool:
    return all(value is None for value in column.to_pylist())


def visible_columns(table: pa.Table) -> list[str]:
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
    if name == "time":
        # Seconds and milliseconds, without the timezone suffix: every
        # timestamp in a result carries the same one, so repeating it on
        # every row costs width and says nothing.
        return str(value)[:23]
    if name == "value" and isinstance(value, float):
        # `repr` rather than a fixed precision: sensors already round to
        # the resolution they claim, so this shows exactly what was
        # stored rather than inventing or hiding digits.
        return repr(value)
    return str(value)


def render(table: pa.Table, limit: int = DEFAULT_LIMIT) -> str:
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
