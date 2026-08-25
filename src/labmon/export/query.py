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
