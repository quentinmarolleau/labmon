"""`labmon monitor` — a panel to leave open beside the experiment."""

from typing import Annotated

import typer

from labmon.cli.options import LogLevelOption, Measurements, SensorIds
from labmon.cli.runtime import configure

HELP = (
    "Watch current sensor values in the terminal, refreshing in place."
    + "\n\n"
    + "Grafana is the right tool for a wall display or for digging"
    + " through history. It is the wrong one for the terminal already"
    + " open next to the experiment, and it is unavailable over a bare"
    + " SSH session — which is exactly when [italic]is the cryostat"
    + " still cold?[/italic] matters most."
    + "\n\n"
    + "Shows what [bold]labmon query latest --stats[/bold] shows, drawn"
    + " again every couple of seconds. [bold]q[/bold] quits,"
    + " [bold]r[/bold] refreshes without waiting."
    + "\n\n"
    + "Needs the [bold]tui[/bold] extra: pip install 'labmon[tui]'"
    + "\n\n"
    + "[bold]Examples[/bold]\n\n"
    + "    labmon monitor\n"
    + "    labmon monitor --since 1h --refresh 5s\n"
    + "    labmon monitor --measurement temperature"
)

Refresh = Annotated[
    str | None,
    typer.Option(
        "--refresh",
        help="How often to redraw, as a duration (2s, 500ms is not a unit;"
        + " use 1s). Default: the configuration file, else 2s",
        metavar="EVERY",
        show_default=False,
    ),
]

Window = Annotated[
    str | None,
    typer.Option(
        "--since",
        help="How much history the statistics cover, same spellings as"
        + " elsewhere. Default: the configuration file, else 15m",
        metavar="WHEN",
        show_default=False,
    ),
]


def monitor(
    measurement: Measurements = None,
    sensor_id: SensorIds = None,
    since: Window = None,
    refresh: Refresh = None,
    log_level: LogLevelOption = "INFO",  # pyright: ignore[reportArgumentType]
) -> None:
    """Watch current sensor values in the terminal, refreshing in place."""
    from labmon.config import load
    from labmon.export.window import WindowError, parse_duration, parse_instant

    configure(log_level)
    settings = load().monitor

    cadence = settings.refresh
    if refresh is not None:
        try:
            cadence = parse_duration(refresh)
        except WindowError as error:
            raise typer.BadParameter(str(error), param_hint="--refresh") from None
        if cadence <= 0:
            raise typer.BadParameter(
                "every zero seconds is a busy loop, not a fast panel",
                param_hint="--refresh",
            )

    window = settings.window
    if since is not None:
        try:
            # Checked before the screen is cleared: a mistake that
            # surfaces on the first tick has already taken the terminal.
            _ = parse_instant(since)
        except WindowError as error:
            raise typer.BadParameter(str(error), param_hint="--since") from None
        window = since

    try:
        from labmon.cli.tui import Panel
    except ImportError as error:
        # Only when Textual is genuinely absent. Reporting every
        # ImportError raised while loading that module as "the extra is
        # missing" would misdiagnose a renamed Textual symbol after an
        # upgrade, and send somebody to reinstall a package they have.
        import importlib.util
        import sys

        if importlib.util.find_spec("textual") is not None:
            raise
        raise typer.BadParameter(
            "labmon monitor needs Textual, which is not installed in"
            + f" {sys.executable}."
            + " Install it with: pip install 'labmon[tui]' — or, for an"
            + " editable checkout installed as a tool,"
            + " uv tool install --editable '.[tui]' --force"
        ) from error

    Panel(
        measurements=measurement,
        sensor_ids=sensor_id,
        window=window,
        refresh=cadence,
    ).run()
