"""Running the "which readings?" question against the database.

The flags themselves are in `labmon.cli.options`; this is the one place
that turns them into rows, so `query` and `export` cannot read the
database in subtly different ways.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import pyarrow as pa

    from labmon.cli.roster import Known
    from labmon.export.window import Window

logger: logging.Logger = logging.getLogger(__name__)


def read(
    measurements: Sequence[str] | None,
    sensor_ids: Sequence[str] | None,
    since: str,
    until: str | None,
) -> "tuple[pa.Table, Window]":
    """Read the readings the selection names, and the window they cover.

    The client is closed on every path, including a failed query: a CLI
    that leaks the connection leaves the server holding it until the
    process exits.

    Everything heavy is imported here rather than at module scope.
    Between them pyarrow and the InfluxDB client cost about 0.3s to
    load, and `labmon --help`, a tab completion and `labmon mock-sensor`
    all reach this module without ever calling this function.
    """
    from labmon.export.query import fetch, resolve_measurements
    from labmon.export.table import combine, normalise
    from labmon.export.window import Window
    from labmon.influx import get_client

    window = Window.parse(since, until)
    wanted_measurements = list(measurements or ())
    wanted_sensors = list(sensor_ids or ())

    client = get_client()
    try:
        resolved = resolve_measurements(client, wanted_measurements)
        parts = [
            normalise(fetch(client, name, window, wanted_sensors), name)
            for name in resolved
        ]
    finally:
        client.close()

    table = combine(parts)
    logger.debug(
        "read readings",
        extra={"rows": table.num_rows, "measurements": len(resolved)},
    )
    return table, window


def read_latest(
    measurements: Sequence[str] | None,
    sensor_ids: Sequence[str] | None,
    since: str,
    until: str | None,
    stats: bool = False,
) -> "tuple[pa.Table, Window]":
    """The most recent reading each sensor produced within the window.

    Shares the client handling and the measurement allowlist with
    `read`, and differs only in the query it runs — so a sensor visible
    to one is visible to the other.

    `stats` asks for the window's mean, standard deviation and reading
    count alongside. They come from the same grouping as the value, so
    they cost no extra round trip and describe the same window.
    """
    from labmon.export.query import fetch_latest, resolve_measurements
    from labmon.export.window import Window
    from labmon.influx import get_client

    window = Window.parse(since, until)
    client = get_client()
    try:
        resolved = resolve_measurements(client, list(measurements or ()))
        table = fetch_latest(
            client, resolved, window, list(sensor_ids or ()), stats=stats
        )
    finally:
        client.close()

    logger.debug(
        "read latest",
        extra={"sensors": table.num_rows, "measurements": len(resolved)},
    )
    return table, window


def reporting_measurements(
    sensor_id: str, since: str, until: str | None = None
) -> list[str]:
    """The measurements `sensor_id` still has readings in, in the window.

    Asked after a sensor is dropped from the roster, to say whether the
    database agrees that it is gone. Runs the same latest-query as
    everything else, narrowed to the one sensor, so what it reports is
    exactly what `labmon query latest` would find.

    The rows are checked against the sensor asked for rather than
    trusted. Naming a sensor that is not the one enquired about would
    turn this into misinformation, and it is one comparison to be sure.
    """
    table, _window = read_latest(None, [sensor_id], since, until)
    sensors = table.column("sensor_id").to_pylist()
    measurements = table.column("measurement").to_pylist()
    return sorted(
        {
            str(name)
            for sensor, name in zip(sensors, measurements, strict=True)
            if name and str(sensor) == sensor_id
        }
    )


def known_from(table: "pa.Table") -> "list[Known]":
    """The roster entries a latest-query result describes."""
    from labmon.cli.roster import Known

    columns = {name: table.column(name).to_pylist() for name in table.column_names}
    entries: list[Known] = []
    for index in range(table.num_rows):
        sensor = columns["sensor_id"][index]
        raw = columns["time"][index]
        if sensor is None or raw is None:
            continue
        # `to_pylist` is typed as returning objects; the column is a
        # timestamp, and a column with no zone is UTC because that is
        # what the latest query stamps in.
        stamp = cast(datetime, raw)
        entries.append(
            Known(
                sensor_id=str(sensor),
                measurement=str(_at(columns, "measurement", index)),
                unit=str(_at(columns, "unit", index)),
                last_seen=stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC),
            )
        )
    return entries


def _at(columns: "dict[str, list[object]]", name: str, index: int) -> object:
    """One optional column's value, or an empty string when absent."""
    values = columns.get(name)
    if values is None or values[index] is None:
        return ""
    return values[index]


def read_latest_with_roster(
    measurements: Sequence[str] | None,
    sensor_ids: Sequence[str] | None,
    since: str,
    until: str | None,
    stats: bool = False,
) -> "tuple[pa.Table, list[Known]]":
    """The latest readings, plus every sensor the roster remembers.

    The roster is unioned in, never substituted: a cached sensor the
    query did not return is carried through so it can be shown as
    silent, and a live sensor the cache had not seen is added to it. A
    stale cache can therefore only ever add a row, never hide one.

    Narrowing by `--sensor-id` narrows the roster too, so asking about
    one sensor does not print the rest of the lab.
    """
    from labmon.cli.roster import cache_path, load, merge, save

    table, _window = read_latest(measurements, sensor_ids, since, until, stats)
    live = known_from(table)

    path = cache_path()
    remembered = merge(load(path), live)
    try:
        save(path, remembered)
    except OSError as error:
        # A cache that cannot be written is a degraded experience, not a
        # failed command: everything the query returned is still correct.
        logger.warning("could not write the roster cache", extra={"reason": str(error)})

    # The roster is narrowed by the same flags as the query. Asking for
    # temperatures and being shown a silent pressure gauge answers a
    # question nobody asked.
    wanted_sensors = set(sensor_ids or ())
    wanted_measurements = set(measurements or ())
    # Compared on the same key the roster is stored under — a sensor and
    # a measurement — so a sensor that reports to two tables can be
    # current in one and silent in the other.
    seen = {entry.key for entry in live}
    silent = [
        entry
        for key, entry in sorted(remembered.items())
        if key not in seen
        and (not wanted_sensors or entry.sensor_id in wanted_sensors)
        and (not wanted_measurements or entry.measurement in wanted_measurements)
    ]
    return table, silent
