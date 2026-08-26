"""`labmon sensors` — what labmon remembers, and how to correct it."""

from typing import Annotated

import typer

from labmon.cli.options import LogLevelOption, Measurements, SensorIds, Since, Until
from labmon.cli.runtime import configure

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
    since: Since = "24h",
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
        return

    live: set[str] = set()
    if refresh:
        table, _silent = selection.read_latest_with_roster(
            measurement, sensor_id, since, until
        )
        live = {str(name) for name in table.column("sensor_id").to_pylist()}
        # read_latest_with_roster has already merged and saved; reloading
        # rather than rebuilding keeps one writer for the file.
        known = load(path)
    else:
        known = load(path)

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
