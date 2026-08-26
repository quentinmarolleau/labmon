"""Turning an export request into SQL, and running it.

Measurement names cannot be bound as query parameters — SQL allows a
parameter where a *value* goes, never where an identifier goes. So the
names that reach a FROM clause here are never the ones the user typed:
they are matched against the tables the server reports, and a name that
does not match is refused. Everything else — sensor ids, time bounds —
is bound as a parameter and never interpolated.
"""

import logging
from collections.abc import Iterable, Sequence
from typing import Protocol, cast

import pyarrow as pa

from labmon.export.window import Window

logger: logging.Logger = logging.getLogger(__name__)

# The schema InfluxDB 3 puts user tables in. `SHOW TABLES` also returns
# `information_schema` and `system` tables, which are not measurements
# and would turn `--measurement` defaulting to "everything" into an
# export of the server's own query log.
_USER_SCHEMA = "iox"

# Columns an export reads when the table has them. A table written by
# something other than labmon may have none of the optional ones, so
# each is included only after the server confirms it exists.
TIME_COLUMN = "time"
VALUE_COLUMN = "value"
OPTIONAL_COLUMNS: tuple[str, ...] = (
    "sensor_id",
    "unit",
    "calibration_id",
    "input_volts",
)


class Queryable(Protocol):
    """The one client method this module needs, so tests can supply a fake."""

    def query(
        self,
        query: str,
        language: str = ...,
        mode: str = ...,
        database: str | None = ...,
        **kwargs: object,
    ) -> pa.Table: ...


class QueryError(RuntimeError):
    """A measurement that does not exist, or a query the server refused."""


def list_measurements(client: Queryable) -> tuple[str, ...]:
    """Every user table in the configured database, sorted.

    Sorted so an export of "everything" concatenates in a stable order
    and two runs over unchanged data produce byte-identical files.
    """
    table = client.query("SHOW TABLES")
    rows = table.to_pydict()
    schemas = cast(list[str], rows.get("table_schema", []))
    names = cast(list[str], rows.get("table_name", []))
    return tuple(
        sorted(
            name
            for schema, name in zip(schemas, names, strict=True)
            if schema == _USER_SCHEMA
        )
    )


def resolve_measurements(
    client: Queryable, requested: Sequence[str]
) -> tuple[str, ...]:
    """Validate requested names against what the server actually has.

    This is the allowlist that makes interpolating a name into FROM safe:
    nothing reaches the SQL that the server did not just name to us.
    """
    available = list_measurements(client)
    if not requested:
        return available

    known = set(available)
    missing = [name for name in requested if name not in known]
    if missing:
        raise QueryError(
            f"no measurement named {', '.join(repr(name) for name in missing)}."
            + f" Available: {', '.join(available) if available else '(none)'}"
        )
    # Deduplicated, and in the order the server reports rather than the
    # order they were typed, for the stable-output reason above.
    requested_set = set(requested)
    return tuple(name for name in available if name in requested_set)


def columns_of(client: Queryable, measurement: str) -> frozenset[str]:
    """Which columns `measurement` has, asked rather than assumed.

    A `SELECT unit FROM t` against a table with no `unit` is a hard error
    from the server, so the column list decides what the SELECT contains.
    """
    table = client.query(
        "SELECT column_name FROM information_schema.columns"
        + " WHERE table_schema = $schema AND table_name = $table",
        query_parameters={"schema": _USER_SCHEMA, "table": measurement},
    )
    return frozenset(cast(list[str], table.to_pydict().get("column_name", [])))


def _select_clause(present: frozenset[str]) -> str:
    wanted = [TIME_COLUMN, VALUE_COLUMN]
    wanted.extend(name for name in OPTIONAL_COLUMNS if name in present)
    return ", ".join(f'"{name}"' for name in wanted)


def fetch(
    client: Queryable,
    measurement: str,
    window: Window,
    sensor_ids: Iterable[str] = (),
) -> pa.Table:
    """Read one measurement over `window`, optionally narrowed to sensors.

    Rows come back ordered by time so a single-measurement export is
    already in the order a reader expects, and a multi-measurement one
    needs only a merge rather than a full sort.
    """
    present = columns_of(client, measurement)
    if TIME_COLUMN not in present or VALUE_COLUMN not in present:
        raise QueryError(
            f"{measurement!r} has no {TIME_COLUMN}/{VALUE_COLUMN} columns,"
            + " so it does not hold readings this tool can export"
        )

    parameters: dict[str, str] = {
        "since": window.since.isoformat(),
        "until": window.until.isoformat(),
    }
    conditions = [
        f'"{TIME_COLUMN}" >= CAST($since AS TIMESTAMP)',
        f'"{TIME_COLUMN}" < CAST($until AS TIMESTAMP)',
    ]

    wanted_sensors = tuple(sensor_ids)
    if wanted_sensors:
        if "sensor_id" not in present:
            # Asking for a sensor from a table that does not record one
            # can only ever return nothing; saying so beats an empty file.
            raise QueryError(
                f"{measurement!r} has no sensor_id column, so it cannot be"
                + " filtered by --sensor-id"
            )
        placeholders: list[str] = []
        for index, sensor in enumerate(wanted_sensors):
            name = f"sensor_{index}"
            parameters[name] = sensor
            placeholders.append(f"${name}")
        conditions.append(f'"sensor_id" IN ({", ".join(placeholders)})')

    sql = (
        f"SELECT {_select_clause(present)}"
        + f' FROM "{measurement}"'
        + f" WHERE {' AND '.join(conditions)}"
        + f' ORDER BY "{TIME_COLUMN}"'
    )
    logger.debug(
        "querying measurement",
        extra={"measurement": measurement, "sensors": len(wanted_sensors)},
    )
    return client.query(sql, query_parameters=parameters)


# --------------------------------------------------------------------------
# The latest reading from each sensor
# --------------------------------------------------------------------------

SENSOR_COLUMN = "sensor_id"


# The SQL type each optional column is cast to when a table lacks it.
# Needed because a UNION arm that selects a bare NULL has no type for the
# planner to reconcile against the arm that selects a real column.
_NULL_TYPES: dict[str, str] = {
    "unit": "VARCHAR",
    "calibration_id": "VARCHAR",
    "input_volts": "DOUBLE",
}


# The statistics a window can be summarised by, as SQL expression and
# output name. Sample standard deviation rather than population: a
# single reading in the window then yields NULL, which renders blank,
# and "the spread of one reading is zero" is a claim worth not making.
STATS_COLUMNS: tuple[str, ...] = ("mean", "sd", "n")
_STATS_SQL: tuple[tuple[str, str], ...] = (
    (f'avg("{VALUE_COLUMN}")', "mean"),
    (f'stddev("{VALUE_COLUMN}")', "sd"),
    (f'count("{VALUE_COLUMN}")', "n"),
)


def _latest_branch(
    measurement: str,
    present: frozenset[str],
    shared: Sequence[str],
    sensor_placeholders: Sequence[str],
    stats: bool = False,
) -> str:
    """One arm of the UNION, reducing a table to a row per sensor.

    Builds SQL text; it does not touch the database. `fetch_latest`
    calls this once per measurement and sends the arms together as a
    single query.

    **`GROUP BY sensor_id` is the whole idea.** It sorts every row in
    the table into piles, one per sensor, and yields one output row per
    pile — twenty thousand `cryo-77k` readings become a single
    `cryo-77k` row.

    Collapsing a pile means every column has to answer "which of these
    values do I show?", which is what the functions are for.
    `max(time)` is the newest timestamp in the pile, and
    `last_value(value ORDER BY time)` sorts the pile by time and takes
    the value from the end — the reading that goes with that timestamp.
    A bare `value` would not compile: "the value of twenty thousand
    rows" is not a question with one answer, and SQL insists on being
    told which one is wanted.

    `last_value` was chosen over `DISTINCT ON (sensor_id)` and over
    `ROW_NUMBER() OVER (PARTITION BY sensor_id ORDER BY time DESC)`. All
    three plan on this server; this one is the shortest to generate and
    measured marginally the fastest.

    The `WHERE` runs before the grouping, so it shrinks the piles first
    — only rows inside the window, and only the sensors asked for.

    Three details that are not obvious:

    - the measurement is selected as a constant, the same on every row.
      Each measurement is a separate table, so once rows from several of
      them are stacked nothing in the data says which table a row came
      from. This stamps it on.
    - `$since`, `$until` and `$sensor_0` are placeholders. Their values
      travel beside the query and are never pasted into its text, so a
      sensor named `\'; DROP TABLE` is only a sensor that does not
      exist. The table name *is* interpolated, safe because it came from
      `resolve_measurements` matching it against the list the server
      itself reported.
    With `stats`, the same pile is also summarised — its mean, its
    standard deviation and how many readings it holds. These are extra
    aggregate functions in a grouping that is already happening, over
    rows `last_value` is already reading, so they add no scan and no
    round trip. They also cannot describe a different window from the
    value they sit beside, which a second query could.

    - a column this table lacks is selected as `CAST(NULL AS ...)`.
      `UNION ALL` stacks rows vertically and requires every arm to have
      the same columns in the same order; the cast is needed because a
      bare NULL has no type for the planner to reconcile against the arm
      holding the real column.

    What comes out, for one table with every optional column and a
    request narrowed to one sensor::

        SELECT \'temperature\' AS "measurement",
               "sensor_id",
               max("time")                                  AS "time",
               last_value("value" ORDER BY "time")          AS "value",
               last_value("unit" ORDER BY "time")           AS "unit",
               last_value("calibration_id" ORDER BY "time") AS "calibration_id",
               last_value("input_volts" ORDER BY "time")    AS "input_volts"
        FROM "temperature"
        WHERE "time" >= CAST($since AS TIMESTAMP)
          AND "time" <  CAST($until AS TIMESTAMP)
          AND "sensor_id" IN ($sensor_0)
        GROUP BY "sensor_id"
    """
    columns = [
        f"'{measurement}' AS \"measurement\"",
        f'"{SENSOR_COLUMN}"',
        f'max("{TIME_COLUMN}") AS "{TIME_COLUMN}"',
        f'last_value("{VALUE_COLUMN}" ORDER BY "{TIME_COLUMN}") AS "{VALUE_COLUMN}"',
    ]
    for optional in shared:
        if optional in present:
            columns.append(
                f'last_value("{optional}" ORDER BY "{TIME_COLUMN}") AS "{optional}"'
            )
        else:
            # Every arm must project the same columns, so a table that
            # lacks one contributes a typed NULL rather than being narrower.
            columns.append(f'CAST(NULL AS {_NULL_TYPES[optional]}) AS "{optional}"')

    if stats:
        columns.extend(f'{expression} AS "{name}"' for expression, name in _STATS_SQL)

    conditions = [
        f'"{TIME_COLUMN}" >= CAST($since AS TIMESTAMP)',
        f'"{TIME_COLUMN}" < CAST($until AS TIMESTAMP)',
    ]
    if sensor_placeholders:
        conditions.append(f'"{SENSOR_COLUMN}" IN ({", ".join(sensor_placeholders)})')

    return (
        f"SELECT {', '.join(columns)}"
        + f' FROM "{measurement}"'
        + f" WHERE {' AND '.join(conditions)}"
        + f' GROUP BY "{SENSOR_COLUMN}"'
    )


def fetch_latest(
    client: Queryable,
    measurements: Sequence[str],
    window: Window,
    sensor_ids: Iterable[str] = (),
    stats: bool = False,
) -> pa.Table:
    """The most recent reading each sensor produced within `window`.

    One round trip for every measurement, as a UNION of one arm per
    table. The round trips are what cost — the scan itself is cheap — so
    asking six times would be six times the latency for the same answer.

    A table with no `sensor_id` is skipped rather than refused: "the
    latest per sensor" has no meaning there, and a measurement written by
    something other than labmon may legitimately not have one. Refusing
    the whole command because one table is unusual would be worse than
    leaving it out.

    `stats` adds the window's mean, standard deviation and reading count
    beside each latest value. Measured against six tables on the demo
    stack, asking for them changed a 24-hour query from 70.8 ms to
    63.5 ms — that is, the difference is below the noise, because the
    rows are being scanned either way.
    """
    parameters: dict[str, str] = {
        "since": window.since.isoformat(),
        "until": window.until.isoformat(),
    }
    placeholders: list[str] = []
    for index, sensor in enumerate(sensor_ids):
        name = f"sensor_{index}"
        parameters[name] = sensor
        placeholders.append(f"${name}")

    usable: list[tuple[str, frozenset[str]]] = []
    for measurement in measurements:
        present = columns_of(client, measurement)
        if not {TIME_COLUMN, VALUE_COLUMN, SENSOR_COLUMN} <= present:
            logger.debug(
                "measurement has no per-sensor readings",
                extra={"measurement": measurement},
            )
            continue
        usable.append((measurement, present))

    # The optional columns any usable table has, so every arm can project
    # the same set — the union of what is available, not the intersection,
    # so one plain table does not hide another's provenance.
    shared = [
        optional
        for optional in OPTIONAL_COLUMNS
        if optional != SENSOR_COLUMN
        and any(optional in present for _, present in usable)
    ]

    branches = [
        _latest_branch(measurement, present, shared, placeholders, stats)
        for measurement, present in usable
    ]

    if not branches:
        return _empty_latest(stats)

    sql = "\nUNION ALL\n".join(branches)
    logger.debug("querying latest", extra={"measurements": len(branches)})
    return client.query(sql, query_parameters=parameters)


def _empty_latest(stats: bool = False) -> pa.Table:
    """The shape `fetch_latest` returns when nothing can be asked.

    Carries the statistics columns when they were asked for. The
    renderer decides what to show by which columns are present, so an
    empty result that dropped them would quietly change the view rather
    than show it with no rows.
    """
    columns: dict[str, pa.Array] = {
        "measurement": pa.array([], pa.string()),
        SENSOR_COLUMN: pa.array([], pa.string()),
        TIME_COLUMN: pa.array([], pa.timestamp("ms", tz="UTC")),
        VALUE_COLUMN: pa.array([], pa.float64()),
    }
    if stats:
        columns["mean"] = pa.array([], pa.float64())
        columns["sd"] = pa.array([], pa.float64())
        columns["n"] = pa.array([], pa.int64())
    return pa.table(columns)
