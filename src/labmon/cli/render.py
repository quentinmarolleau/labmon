"""Rendering readings as a table a person reads in a terminal.

Separate from the file writers because the goals conflict: a file wants
every column and full precision so nothing is lost, while a terminal
wants the few columns that carry meaning, aligned, in a width that fits.
"""

from collections.abc import Container, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, cast

from labmon.cli.age import Freshness, describe, freshness
from labmon.cli.quantity import at_the_precision_of, quote, show

if TYPE_CHECKING:
    import pyarrow as pa

    from labmon.cli.roster import Known

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

# Columns holding a measured quantity, shown at a readable magnitude.
# `input_volts` is here for the export-shaped tables that carry it, even
# though the terminal views leave it out.
_NUMERIC_COLUMNS: frozenset[str] = frozenset({"value", "input_volts"})


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


def _cell(name: str, value: object, tz: tzinfo = UTC) -> str:
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
        #
        # Moved into the reader's zone first. The stored instant does not
        # change; where somebody standing next to the experiment reads it
        # does, and "was the cryostat cold at 3 a.m." is a question about
        # their clock, not about UTC.
        return (
            value.astimezone(tz)
            .replace(tzinfo=None)
            .isoformat(sep=" ", timespec="milliseconds")
        )
    if name in _NUMERIC_COLUMNS and isinstance(value, float):
        # Plain where a decimal reads well, scientific where it does not,
        # and nothing rounded away in either — see `labmon.cli.quantity`.
        return show(value)
    return str(value)


def render(table: "pa.Table", limit: int = DEFAULT_LIMIT, tz: tzinfo = UTC) -> str:
    """Format `table` as an aligned table, most recent rows last.

    A limit of 0 means every row. When rows are dropped, the footer says
    so — a silently truncated table is one somebody draws a conclusion
    from.

    `tz` is the zone timestamps are shown in, from the user's config.
    It defaults to UTC so a caller with no configuration to hand — a
    test, or a path that has not been given one — behaves as before.
    """
    total = table.num_rows
    if total == 0:
        return "no readings matched"

    shown = table if limit <= 0 or total <= limit else table.slice(total - limit, limit)
    names = visible_columns(shown)
    columns = {name: shown.column(name).to_pylist() for name in names}

    rows = [
        [_cell(name, columns[name][index], tz) for name in names]
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

# `measurement` leads because the rows are sorted by it. A table
# ordered by a column that is not the first one it shows looks
# arbitrary, which is the readability problem the ordering was meant to
# solve.
#
# `mean`, `sd` and `n` are last because they are only sometimes there:
# `render_latest` shows a column when the table carries it, so asking
# the query for statistics is the only switch, and the view cannot
# disagree with what was fetched.
LATEST_COLUMNS: tuple[str, ...] = (
    "measurement",
    "sensor_id",
    "value",
    "unit",
    "age",
    "mean",
    "sd",
    "n",
)

# SGR codes rather than a styling library: this is the only colour the
# CLI emits, and reaching for one would pull an import into a path the
# startup work deliberately keeps clear.
_STYLES: dict[Freshness, str] = {
    Freshness.FRESH: "",
    Freshness.AGEING: "\x1b[33m",
    Freshness.STALE: "\x1b[31m",
}
_RESET = "\x1b[0m"


@dataclass(frozen=True)
class LatestRow:
    """One sensor's line, independent of how it is finally drawn.

    Built once and shared: the plain table `labmon query latest` prints
    and the panel `labmon monitor` draws are two presentations of these,
    so they cannot disagree about what a sensor's latest reading is or
    about how stale it has become.
    """

    cells: tuple[str, ...]
    age: timedelta
    state: Freshness


def latest_rows(
    table: "pa.Table",
    now: datetime,
    *,
    silent: "Sequence[Known]" = (),
    round_values: bool = False,
) -> tuple[tuple[str, ...], list[LatestRow]]:
    """The columns worth showing, and one row per sensor, in order.

    **Ordered by measurement, then by sensor.** Alphabetically, and by
    nothing else. Age was the obvious key on a view that prints once and
    exits — it put the sensor that had stopped on the last line, where
    an eye lands. It is unusable on a panel that redraws every two
    seconds: every row moves on every tick, so no row can be followed
    and reading one value means finding it again first. Staleness is
    carried by colour, which does not depend on position.

    A sensor the roster remembers but the query did not return sorts
    among the others rather than at the end. It is remembered, not
    exiled, and its blank value already says what it is.

    `round_values` shows each reading at the precision its own deviation
    over the window justifies. It is off for `labmon query latest`,
    which promises the reading exactly as stored, and on for the panel,
    which is read at a glance from across a room and where nineteen
    digits of a beam position crowd out the rest of the row. Nothing is
    lost: the exact value is one `labmon query latest` away.
    """
    columns = {name: table.column(name).to_pylist() for name in table.column_names}
    present = tuple(name for name in LATEST_COLUMNS if name == "age" or name in columns)

    rows: list[tuple[tuple[str, str], LatestRow]] = []
    for index in range(table.num_rows):
        age = now - _as_datetime(columns["time"][index])
        # `mean` and `sd` are rounded against each other, so neither can
        # be formatted alone — see `labmon.cli.quantity.quote`.
        summary = _summary(columns, index, round_values=round_values)
        cells = tuple(
            describe(age)
            if name == "age"
            else summary[name]
            if name in summary
            else _cell(name, columns[name][index])
            for name in present
        )
        rows.append(
            (
                (
                    str(_at(columns, "measurement", index)),
                    str(columns["sensor_id"][index]),
                ),
                LatestRow(cells=cells, age=age, state=freshness(age)),
            )
        )

    # Sensors the query returned nothing for, carried in from the roster.
    # Their value column is left blank rather than filled with the last
    # reading they ever sent: printed beside a fresh number from another
    # sensor, an old one reads as current.
    for entry in silent:
        age = now - entry.last_seen
        rows.append(
            (
                (entry.measurement, entry.sensor_id),
                LatestRow(
                    cells=tuple(_silent_cell(name, entry, age) for name in present),
                    age=age,
                    state=freshness(age),
                ),
            )
        )

    rows.sort(key=lambda item: item[0])
    return present, [row for _key, row in rows]


def _at(columns: "dict[str, list[object]]", name: str, index: int) -> object:
    """One column's value at `index`, or an empty string when absent."""
    values = columns.get(name)
    if values is None or values[index] is None:
        return ""
    return values[index]


def render_latest(
    table: "pa.Table",
    now: datetime,
    *,
    colour: bool = False,
    silent: "Sequence[Known]" = (),
) -> str:
    """One row per sensor, by measurement then sensor, with its age.

    Colour is applied after padding, never before. An escape code counts
    toward `len`, so a cell coloured first pads to fewer visible
    characters than its neighbours and silently shortens its column.

    The window statistics appear when `table` carries them. A sensor
    the roster remembers but the query did not return has none, and its
    cells are left blank for the same reason its value is.
    """
    total = table.num_rows + len(silent)
    if total == 0:
        return "no readings matched"

    present, entries = latest_rows(table, now, silent=silent)
    rows = [row.cells for row in entries]

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
    for row, entry in zip(rows, entries, strict=True):
        padded = "  ".join(
            cell.ljust(width) for cell, width in zip(row, widths, strict=True)
        ).rstrip()
        style = _STYLES[entry.state] if colour else ""
        lines.append(f"{style}{padded}{_RESET}" if style else padded)

    lines.append("")
    lines.append(f"{total} sensor{'s' if total != 1 else ''}")
    if silent:
        quiet = len(silent)
        lines.append(
            f"{quiet} of them reported nothing in this window"
            + " — remembered from a previous run"
        )
    return "\n".join(lines)


def _summary(
    columns: "dict[str, list[object]]", index: int, *, round_values: bool = False
) -> dict[str, str]:
    """The cells one row's statistics decide, rounded against each other.

    Empty when the table carries no statistics, which is how a result
    fetched without them ends up showing none.
    """
    if "mean" not in columns:
        return {}
    mean = columns["mean"][index]
    sd = columns["sd"][index]
    if not isinstance(mean, float):
        # No readings to average — the row came from somewhere the
        # aggregate could not be computed.
        return {"mean": "", "sd": ""}
    spread = sd if isinstance(sd, float) else None
    shown, deviation = quote(mean, spread)
    cells = {"mean": shown, "sd": deviation}

    if round_values:
        reading = columns["value"][index]
        if isinstance(reading, float):
            matched = at_the_precision_of(reading, spread)
            if matched is not None:
                cells["value"] = matched
    return cells


def _silent_cell(name: str, entry: "Known", age: timedelta) -> str:
    """One cell for a sensor the query returned no reading for."""
    if name == "age":
        return describe(age)
    if name == "sensor_id":
        return entry.sensor_id
    if name == "measurement":
        return entry.measurement
    if name == "unit":
        return entry.unit
    # `value` and anything else: nothing was read, so nothing is shown.
    return ""


def _as_datetime(stamp: object) -> datetime:
    """A timestamp column entry as an aware datetime.

    pyarrow hands back a naive value for a column with no zone, and the
    latest query stamps in UTC, so an absent zone means UTC rather than
    local time.
    """
    moment = cast(datetime, stamp)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


ROSTER_COLUMNS: tuple[str, ...] = ("sensor_id", "measurement", "unit", "age", "source")


def render_roster(
    known: "Mapping[tuple[str, str], Known]",
    *,
    live: "Container[tuple[str, str]] | None",
    now: datetime,
    colour: bool = False,
) -> str:
    """The remembered sensors, in order, saying which are reporting.

    Ordered by measurement then sensor, the same as `render_latest`.
    Three views of the same kind of table sorting three different ways
    would make a sensor hard to find in whichever one you were not
    looking at.

    The `source` column is what earns this view its place: it states
    whether a sensor is reporting now or is only remembered, which makes
    the union rule visible instead of magic.

    It appears only when `live` is known — that is, when the database was
    actually asked. A plain listing touches nothing, so the column could
    only ever read "cached", including beside a sensor reporting at that
    moment. Omitting it beats printing one that misleads.
    """
    if not known:
        return "nothing remembered yet"

    entries = sorted(
        known.values(), key=lambda entry: (entry.measurement, entry.sensor_id)
    )
    names = ROSTER_COLUMNS if live is not None else ROSTER_COLUMNS[:-1]
    rows = [
        [
            entry.sensor_id,
            entry.measurement,
            entry.unit,
            describe(now - entry.last_seen),
            *(["live" if entry.key in live else "cached"] if live is not None else []),
        ]
        for entry in entries
    ]
    styles = [
        _STYLES[freshness(now - entry.last_seen)] if colour else "" for entry in entries
    ]

    widths = [
        max(len(name), *(len(row[position]) for row in rows))
        for position, name in enumerate(names)
    ]
    lines = [
        "  ".join(name.ljust(width) for name, width in zip(names, widths, strict=True)),
        "  ".join("-" * width for width in widths),
    ]
    for row, style in zip(rows, styles, strict=True):
        padded = "  ".join(
            cell.ljust(width) for cell, width in zip(row, widths, strict=True)
        ).rstrip()
        lines.append(f"{style}{padded}{_RESET}" if style else padded)

    lines.append("")
    lines.append(f"{len(entries)} sensor{'s' if len(entries) != 1 else ''}")
    return "\n".join(lines)
