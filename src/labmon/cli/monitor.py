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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from labmon.cli.render import LatestRow
    from labmon.config import Display, Panel

from labmon.cli.age import Freshness, describe

logger: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Snapshot:
    """What one refresh produced, successful or not.

    Carries rows rather than rendered text. The plain table
    `labmon query latest` prints and the panel `labmon monitor` draws
    are two presentations of the same `LatestRow` values, which is what
    keeps them from disagreeing about a reading or its staleness while
    still letting one of them have a border and a colour scheme.

    A failure is carried rather than raised. A panel that dies on one
    unreachable moment is useless next to an experiment, and the window
    is re-queried every tick anyway, so the recovery is automatic.
    """

    taken: datetime
    columns: tuple[str, ...] = ()
    rows: tuple["LatestRow", ...] = ()
    quiet: int = 0
    error: str | None = None

    @property
    def sensors(self) -> int:
        """How many sensors the table describes."""
        return len(self.rows)


def take(
    measurements: Sequence[str] | None,
    sensor_ids: Sequence[str] | None,
    window: str,
    *,
    now: datetime | None = None,
    display: "Sequence[Display]" = (),
) -> Snapshot:
    """Query the window and lay it out in rows, or say why that failed."""
    from influxdb_client_3.exceptions.exceptions import InfluxDB3ClientError

    from labmon.cli import selection
    from labmon.cli.render import latest_rows
    from labmon.export.query import QueryError
    from labmon.export.window import WindowError

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
        return Snapshot(taken=moment, error=str(error))

    columns, rows = latest_rows(table, moment, silent=silent, display=display)
    return Snapshot(taken=moment, columns=columns, rows=tuple(rows), quiet=len(silent))


@dataclass(frozen=True)
class Tile:
    """One configured panel, paired with what the last tick found.

    Made even when the sensor reported nothing. Silently dropping a
    configured tile is the failure this whole view exists to prevent:
    the tile somebody put in their layout is the one they are watching
    for, and an empty space says nothing while an empty tile says the
    reading has not arrived.

    `found` is whether the reading is from this window. A tile the
    roster remembers still carries the last value it saw — what an
    instrument was reading when it went quiet is usually the question —
    and `found` is what stops that number being drawn as though it were
    current.
    """

    heading: str
    measurement: str
    reading: str
    unit: str
    age: str
    state: Freshness
    found: bool
    alarm: str | None = None


def tiles(
    snapshot: Snapshot,
    panels: "Sequence[Panel]",
    display: "Sequence[Display]" = (),
) -> list[Tile]:
    """Pair each configured panel with the row that answers it.

    In the order the panels were written. A layout says which tile to
    look at first, and sorting it would discard exactly that — unlike
    the fallback table, where nothing but the alphabet decides.

    A tile's own `precision` and `format` win over the sensor's display
    rule, which is the point of having both: the rule says how the
    instrument is worth reading everywhere, and the tile says how this
    one tile wants it.
    """
    from labmon.cli.quantity import for_display
    from labmon.config import display_for

    by_key = {(row.measurement, row.sensor_id): row for row in snapshot.rows}
    by_sensor: dict[str, list[LatestRow]] = {}
    for row in snapshot.rows:
        by_sensor.setdefault(row.sensor_id, []).append(row)

    made: list[Tile] = []
    for panel in panels:
        row: LatestRow | None = None
        if panel.measurement is not None:
            row = by_key.get((panel.measurement, panel.sensor_id))
        else:
            # No measurement named: take the first alphabetically, which
            # is the only one for almost every sensor. The tile prints
            # whichever it settled on, so the screen is never ambiguous
            # even when the file was.
            candidates = sorted(
                by_sensor.get(panel.sensor_id, ()), key=lambda item: item.measurement
            )
            row = candidates[0] if candidates else None

        if row is None:
            made.append(
                Tile(
                    heading=panel.heading,
                    measurement=panel.measurement or "",
                    reading="",
                    unit="",
                    age="not reporting",
                    state=Freshness.STALE,
                    found=False,
                )
            )
            continue

        reading = ""
        alarm: str | None = None
        if row.reading is not None:
            rule = display_for(display, row.sensor_id, row.measurement)
            reading = for_display(
                row.reading,
                precision=panel.precision
                if panel.precision is not None
                else (rule.precision if rule is not None else None),
                style=panel.format
                if panel.format != "auto"
                else (rule.format if rule is not None else "auto"),
            )
            # Never on a remembered reading. A threshold is a claim
            # about the experiment right now, and an hour-old number
            # cannot support one — the tile is already marked as not
            # reporting, which is the accurate alarm to raise.
            if not row.silent:
                alarm = _alarm(row.reading, panel)

        made.append(
            Tile(
                heading=panel.heading,
                measurement=row.measurement,
                reading=reading,
                unit=row.unit,
                age=describe(row.age),
                state=row.state,
                found=not row.silent and row.reading is not None,
                alarm=alarm,
            )
        )
    return made


def _alarm(reading: float, panel: "Panel") -> str | None:
    """What is wrong with this reading, if anything.

    Text rather than a flag, because a tile that has turned red should
    say which way it went — too hot and too cold are different problems
    and the colour is the same. Written as `> 80` rather than
    `above 80`: the footer also carries the measurement and the age, and
    a tile is only so wide.
    """
    if panel.warn_above is not None and reading > panel.warn_above:
        return f"> {panel.warn_above:g}"
    if panel.warn_below is not None and reading < panel.warn_below:
        return f"< {panel.warn_below:g}"
    return None


def status(snapshot: Snapshot, *, window: str, refresh: float, tz: tzinfo = UTC) -> str:
    """The line under the table: what is being shown, and how often.

    A failure leads, because it is the one thing on the line that
    changes what the numbers above it mean — they are now stale, and
    nothing else on screen says so.

    The refresh time is moved into the reader's zone for the same reason
    a `time` column is. Textual's own header clock is already showing
    local time three lines above this one, so a UTC wall clock here puts
    two clocks on one screen disagreeing by whole hours.
    """
    cadence = f"window {window} · every {seconds(refresh)}"
    if snapshot.error is not None:
        return f"{snapshot.error} — showing the last good reading · {cadence}"

    counted = f"{snapshot.sensors} sensor{'s' if snapshot.sensors != 1 else ''}"
    if snapshot.quiet:
        counted += f", {snapshot.quiet} quiet"
    clock = snapshot.taken.astimezone(tz).strftime("%H:%M:%S")
    return f"{counted} · {cadence} · {clock}"


def seconds(value: float) -> str:
    """A refresh interval, without a trailing `.0` on a whole number."""
    return f"{value:g}s"


# The cadences the panel offers. A short list rather than a free-text
# prompt: the question asked mid-experiment is roughly how often, not
# how many seconds exactly, and picking from six answers it without
# anybody having to type into a panel they are watching.
RATES: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)


def cadences(configured: float) -> tuple[float, ...]:
    """The list to choose from, with `configured` spliced in.

    Whatever somebody wrote in their file is always on the menu, so
    changing the rate to look at something closely and then changing it
    back returns to the interval they chose rather than to the nearest
    round number.
    """
    return tuple(sorted({*RATES, configured}))
