"""Running the "which readings?" question against the database.

The flags themselves are in `labmon.cli.options`; this is the one place
that turns them into rows, so `query` and `export` cannot read the
database in subtly different ways.
"""

import logging
from collections.abc import Sequence

import pyarrow as pa

from labmon.export.query import fetch, resolve_measurements
from labmon.export.table import combine, normalise
from labmon.export.window import Window
from labmon.influx import get_client

logger: logging.Logger = logging.getLogger(__name__)


def read(
    measurements: Sequence[str] | None,
    sensor_ids: Sequence[str] | None,
    since: str,
    until: str | None,
) -> tuple[pa.Table, Window]:
    """Read the readings the selection names, and the window they cover.

    The client is closed on every path, including a failed query: a CLI
    that leaks the connection leaves the server holding it until the
    process exits.
    """
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
