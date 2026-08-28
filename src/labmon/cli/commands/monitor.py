"""`labmon monitor` — a panel to leave open beside the experiment."""

from pathlib import Path
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
    + " [bold]r[/bold] changes the refresh rate, [bold]?[/bold] lists"
    + " the keys."
    + "\n\n"
    + "Needs the [bold]tui[/bold] extra: pip install 'labmon[tui]'"
    + "\n\n"
    + "[bold]Examples[/bold]\n\n"
    + "    labmon monitor\n"
    + "    labmon monitor --config bakeout.toml\n"
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

Layout = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        # `\[` because Typer renders help through Rich, which otherwise
        # reads `[monitor]` as markup for a style it does not have and
        # drops it — leaving the one line that names the section with
        # the name missing.
        help=r"A layout file for one procedure, overriding the \[monitor]"
        + " section of the user configuration",
        metavar="FILE",
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
    config: Layout = None,
    measurement: Measurements = None,
    sensor_id: SensorIds = None,
    since: Window = None,
    refresh: Refresh = None,
    log_level: LogLevelOption = "INFO",  # pyright: ignore[reportArgumentType]
) -> None:
    """Watch current sensor values in the terminal, refreshing in place."""
    from labmon.config import load, load_monitor
    from labmon.export.window import WindowError, parse_duration, parse_instant

    configure(log_level)
    # The user configuration, read once: it carries the reader's zone as
    # well as the [monitor] defaults, and the panel needs both.
    settings_file = load()

    # A layout named on the command line replaces the [monitor] section
    # whole. Merging the two would leave somebody unable to say "just
    # these tiles" without first editing the file they were avoiding.
    settings = load_monitor(config) if config is not None else settings_file.monitor

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
        from labmon.cli.tui import Panel, themes
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

    # Checked here rather than left to Textual, which refuses an unknown
    # theme by raising once the panel is already running and the screen
    # has been taken over. The file is named because a theme is written
    # down once and read back months later.
    if settings.theme not in themes():
        from labmon.config import ConfigError, config_path

        where = config if config is not None else config_path()
        raise ConfigError(
            f"{where}: monitor.theme — {settings.theme!r} is not a theme"
            + f" this panel has. Try one of: {', '.join(themes())}"
        )

    Panel(
        measurements=measurement,
        sensor_ids=sensor_id,
        window=window,
        refresh=cadence,
        panels=settings.panels,
        display=settings.sensors,
        tz=settings_file.timezone,
        theme=settings.theme,
    ).run()
