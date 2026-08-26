"""Running the "which readings?" question against the database.

The flags themselves are in `labmon.cli.options`; this is the one place
that turns them into rows, so `query` and `export` cannot read the
database in subtly different ways.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pyarrow as pa

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
) -> "tuple[pa.Table, Window]":
    """The most recent reading each sensor produced within the window.

    Shares the client handling and the measurement allowlist with
    `read`, and differs only in the query it runs — so a sensor visible
    to one is visible to the other.
    """
    from labmon.export.query import fetch_latest, resolve_measurements
    from labmon.export.window import Window
    from labmon.influx import get_client

    window = Window.parse(since, until)
    client = get_client()
    try:
        resolved = resolve_measurements(client, list(measurements or ()))
        table = fetch_latest(client, resolved, window, list(sensor_ids or ()))
    finally:
        client.close()

    logger.debug(
        "read latest",
        extra={"sensors": table.num_rows, "measurements": len(resolved)},
    )
    return table, window
