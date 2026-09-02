"""The `labmon` command.

    labmon query  --measurement temperature --since 5m
    labmon export --measurement temperature --since 5m --format feather -o test
    labmon mock-sensor --sensor-id room-1 --setpoint 21 --unit °C
    labmon --install-completion fish
    labmon --version

Built on Typer: the help text, the choice validation and the shell
completion all come from the command signatures, so there is nothing
separate to keep in step with them.
"""

from typing import Annotated

import typer

from labmon.cli.commands import export as export_command
from labmon.cli.commands import init as init_command
from labmon.cli.commands import mock_sensor as mock_sensor_command
from labmon.cli.commands import monitor as monitor_command
from labmon.cli.commands import query as query_command
from labmon.cli.commands import reset_database as reset_database_command
from labmon.cli.commands import sensors as sensors_command
from labmon.cli.commands import serial_sensor as serial_sensor_command
from labmon.cli.runtime import reporting
from labmon.version import installed_version

HELP = (
    "[bold]labmon[/bold] — laboratory monitoring."
    + "\n\n"
    + "[bold]query[/bold] and [bold]export[/bold] ask the database the same"
    + " question and differ only in what they do with the answer, so they"
    + " share one set of selection flags: --measurement, --sensor-id,"
    + " --since and --until."
    + "\n\n"
    + "Run [bold]labmon --install-completion[/bold] once to get tab"
    + " completion, which shows each flag's help text as you type it."
)


def _report_version(requested: bool) -> None:
    """Print the installed version and stop.

    Eager, so `labmon --version` answers without Typer first deciding
    that no subcommand was given and printing the help instead.
    """
    if requested:
        typer.echo(f"labmon {installed_version()}")
        raise typer.Exit


def _root(
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_report_version,
            is_eager=True,
            help="Show the installed version and exit.",
        ),
    ] = False,
) -> None:
    """Carry the options that belong to `labmon` itself rather than to a
    subcommand. Typer has one place to put those, and this is it.
    """


def build_app() -> typer.Typer:
    """The Typer application, with every command registered.

    A function rather than a module-level constant so tests can build a
    fresh app, and so importing this module has no side effects.
    """
    app = typer.Typer(
        name="labmon",
        help=HELP,
        no_args_is_help=True,
        add_completion=True,
        rich_markup_mode="rich",
        # A subcommand invoked with no arguments should explain itself
        # rather than start a sensor with every default.
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    # `query` keeps its own behaviour when invoked bare — the log-style
    # table is what it has always meant — and gains subcommands for the
    # other questions worth asking of recorded readings.
    query_app = typer.Typer(
        help=query_command.HELP,
        rich_markup_mode="rich",
        invoke_without_command=True,
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    _ = query_app.callback(invoke_without_command=True)(reporting(query_command.query))
    _ = query_app.command("latest", help=query_command.LATEST_HELP)(
        reporting(query_command.latest)
    )
    app.add_typer(query_app, name="query")
    _ = app.command("sensors", help=sensors_command.HELP)(
        reporting(sensors_command.sensors)
    )
    _ = app.command("monitor", help=monitor_command.HELP)(
        reporting(monitor_command.monitor)
    )
    _ = app.command("export", help=export_command.HELP)(
        reporting(export_command.export)
    )
    _ = app.command("init", help=init_command.HELP)(reporting(init_command.init))
    _ = app.command("reset-database", help=reset_database_command.HELP)(
        reporting(reset_database_command.reset_database)
    )
    _ = app.command("mock-sensor", help=mock_sensor_command.HELP)(
        reporting(mock_sensor_command.mock_sensor)
    )
    _ = app.command("serial-sensor", help=serial_sensor_command.HELP)(
        reporting(serial_sensor_command.serial_sensor)
    )
    _ = app.callback()(_root)
    return app


def main() -> None:
    """CLI entry point (see module docstring for usage examples)."""
    build_app()()
