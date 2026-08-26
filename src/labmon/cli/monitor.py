"""One tick of the terminal panel: the text, and what went wrong.

Separated from the Textual application so that almost all of the panel
can be tested without an event loop, and so that the panel and
`labmon query latest` cannot disagree about what "latest" means — the
same selection layer and the same renderer produce both.

Refreshing re-queries the whole window rather than appending to what it
already has. That is what Grafana does with a SQL datasource, and the
numbers say why: the readings a fifteen-minute window holds cost about
14 ms to pull. Against a two-second cadence that is not worth
optimising, and an incremental design would have to carry per-sensor
watermarks, handle late-arriving points and reconcile after a dropped
connection — real state and real bugs, to save single-digit
milliseconds. Re-querying is also self-healing: after a network blip the
next tick is simply correct, with no gap to detect or backfill.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo

logger: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Snapshot:
    """What one refresh produced, successful or not.

    A failure is carried rather than raised. A panel that dies on one
    unreachable moment is useless next to an experiment, and the window
    is re-queried every tick anyway, so the recovery is automatic.
    """

    body: str
    taken: datetime
    error: str | None = None


def take(
    measurements: Sequence[str] | None,
    sensor_ids: Sequence[str] | None,
    window: str,
    *,
    now: datetime | None = None,
    tz: tzinfo = UTC,
    colour: bool = True,
) -> Snapshot:
    """Query the window and render it, or say why that failed."""
    from influxdb_client_3.exceptions.exceptions import InfluxDB3ClientError

    from labmon.cli import selection
    from labmon.cli.render import render_latest
    from labmon.export.query import QueryError
    from labmon.export.window import WindowError

    _ = tz  # the latest view reports ages, not absolute times
    moment = now or datetime.now(UTC)
    try:
        table, silent = selection.read_latest_with_roster(
            measurements, sensor_ids, window, None, stats=True
        )
    except (
        InfluxDB3ClientError,
        QueryError,
        WindowError,
        OSError,
        KeyError,
    ) as error:
        # The same failures `labmon.cli.runtime` turns into exit codes.
        # Here they mean "no answer this tick" instead: the panel says so
        # on its status line and tries again in two seconds, because one
        # unreachable moment is no reason to tear down a terminal an
        # experiment is being watched on.
        logger.debug("refresh failed", extra={"reason": str(error)})
        return Snapshot(body="", taken=moment, error=str(error))

    return Snapshot(
        body=render_latest(table, now=moment, colour=colour, silent=silent),
        taken=moment,
    )


def status(snapshot: Snapshot, *, window: str, refresh: float) -> str:
    """The line under the table: what is being shown, and how often.

    A failure leads, because it is the one thing on the line that
    changes what the numbers above it mean — they are now stale, and
    nothing else on screen says so.
    """
    cadence = f"window {window} · refreshing every {_seconds(refresh)}"
    if snapshot.error is not None:
        return f"{snapshot.error} — showing the last good reading · {cadence}"
    return f"{snapshot.taken.strftime('%H:%M:%S')} · {cadence}"


def _seconds(value: float) -> str:
    """A refresh interval, without a trailing `.0` on a whole number."""
    return f"{value:g}s"
