"""`labmon sensors` — what labmon remembers, and how to correct it."""

import logging
from typing import Annotated

import typer

from labmon.cli.options import LogLevelOption, Measurements, SensorIds, Since, Until
from labmon.cli.runtime import configure

logger: logging.Logger = logging.getLogger(__name__)

# The window a refresh covers, and the one a forgotten sensor is
# checked against, so the two agree on what "recently" means.
DEFAULT_WINDOW = "24h"

HELP = (
    "List the sensors labmon knows about."
    + "\n\n"
    + "The list is a cache, because a sensor that has gone quiet is the"
    + " one worth finding and a query alone cannot show it: silent for"
    + " longer than the window asked about, it has no row to be stale and"
    + " simply vanishes. The [bold]source[/bold] column says whether a"
    + " sensor is reporting now or is only remembered."
    + "\n\n"
    + "[bold]Examples[/bold]\n\n"
    + "    labmon sensors\n"
    + "    labmon sensors --refresh\n"
    + "    labmon sensors --refresh --since 1w\n"
    + "    labmon sensors --forget old-probe"
)

Refresh = Annotated[
    bool,
    typer.Option(
        "--refresh",
        help="Ask the database which sensors have reported, and remember them",
    ),
]

Forget = Annotated[
    str | None,
    typer.Option(
        "--forget",
        help="Drop one sensor from the list, for an instrument that is gone",
        metavar="SENSOR_ID",
        show_default=False,
    ),
]


def sensors(
    refresh: Refresh = False,
    forget: Forget = None,
    measurement: Measurements = None,
    sensor_id: SensorIds = None,
    since: Since | None = None,
    until: Until = None,
    log_level: LogLevelOption = "INFO",  # pyright: ignore[reportArgumentType]
) -> None:
    """List the sensors labmon knows about."""
    import sys
    from datetime import UTC, datetime

    from labmon.cli import selection
    from labmon.cli.render import render_roster
    from labmon.cli.roster import cache_path, load, save
    from labmon.cli.roster import forget as drop

    configure(log_level)
    path = cache_path()

    if forget is not None and refresh:
        # A refresh would re-add whatever was just forgotten if it still
        # reports, so the two together have no coherent meaning.
        raise typer.BadParameter("--forget cannot be combined with --refresh")

    if forget is not None:
        known = load(path)
        try:
            remaining = drop(known, forget)
        except KeyError:
            raise typer.BadParameter(
                f"no sensor named {forget!r} is remembered", param_hint="--forget"
            ) from None
        save(path, remaining)
        print(f"forgot {forget}")
        _say_if_still_reporting(forget)
        return

    if not refresh and (since is not None or until is not None):
        # A window only bounds the query a refresh runs. Accepting it for
        # a plain listing would let somebody believe it had narrowed
        # something.
        raise typer.BadParameter(
            "--since and --until apply to --refresh; a plain listing reads"
            + " only the remembered list"
        )

    live: set[tuple[str, str]] | None = None
    if refresh:
        table, _silent = selection.read_latest_with_roster(
            measurement, sensor_id, since or DEFAULT_WINDOW, until
        )
        live = set(
            zip(
                (str(name) for name in table.column("sensor_id").to_pylist()),
                (str(name) for name in table.column("measurement").to_pylist()),
                strict=True,
            )
        )
    # read_latest_with_roster has already merged and saved; reloading
    # rather than rebuilding keeps one writer for the file.
    known = load(path)

    # `--measurement` and `--sensor-id` describe a roster entry, so they
    # narrow a plain listing too rather than being quietly ignored.
    wanted_sensors = set(sensor_id or ())
    wanted_measurements = set(measurement or ())
    if wanted_sensors or wanted_measurements:
        known = {
            key: entry
            for key, entry in known.items()
            if (not wanted_sensors or entry.sensor_id in wanted_sensors)
            and (not wanted_measurements or entry.measurement in wanted_measurements)
        }

    if not known:
        print(
            "nothing remembered yet — run `labmon sensors --refresh`"
            + " to ask the database"
        )
        return

    print(
        render_roster(
            known, live=live, now=datetime.now(UTC), colour=sys.stdout.isatty()
        )
    )


def _say_if_still_reporting(sensor_id: str) -> None:
    """Say so when the database still holds readings for a forgotten sensor.

    Forgetting edits the cache, and the cache may only ever add — so a
    sensor that is still writing will be found by the next query and
    remembered again. `forgot X` on its own reads as "it is gone", which
    for a sensor still reporting it is not.

    Best-effort, and deliberately silent when it cannot run: the roster
    has already been written, and a database that is unreachable is no
    reason to fail a command that has finished its work. The condition
    it reports on cannot exist if there is no database to report it.
    """
    from influxdb_client_3.exceptions.exceptions import InfluxDB3ClientError

    from labmon.cli import selection
    from labmon.export.query import QueryError

    try:
        still = selection.reporting_measurements(sensor_id, DEFAULT_WINDOW)
    except (InfluxDB3ClientError, QueryError, OSError, KeyError) as error:
        # The same failures `labmon.cli.runtime` turns into exit codes —
        # a server that is down or refusing the token, a query it will
        # not answer, `INFLUXDB3_AUTH_TOKEN` unset — none of which say
        # anything about a sensor.
        logger.debug(
            "could not check for recent readings", extra={"reason": str(error)}
        )
        return
    if not still:
        return
    print(
        f"it still has readings in {', '.join(still)} from the last"
        + f" {DEFAULT_WINDOW}, so a query over that window will find it"
        + " and remember it again until they age out"
    )
