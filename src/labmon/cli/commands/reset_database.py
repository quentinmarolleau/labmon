"""`labmon reset-database` — drop the database and create it again.

The alternative, before this existed, was `rm -rf .influxdb3/data` in
the repository root: InfluxDB's data is a bind mount rather than a named
volume, so `docker compose down -v` leaves it untouched. A recursive
delete against a hand-typed path is a poor thing to put in a document,
and it needs the stack stopped. This goes through the API instead.

The token is not touched. Tokens belong to the instance rather than to a
database, so every sensor machine's `.env` keeps working across a reset
— which is what makes this safe to run on a stack with clients on it.
"""

import logging
from typing import Annotated, cast

import typer

from labmon import admin
from labmon.cli.commands.init import Database
from labmon.cli.options import LogLevelOption
from labmon.cli.runtime import REFUSED, configure

logger: logging.Logger = logging.getLogger(__name__)

HELP = (
    "Delete every reading in the database, and create it again empty."
    + "\n\n"
    + "[bold]This cannot be undone.[/bold] Confirm by typing the database's"
    + " name, or pass [bold]--yes[/bold] in a script. The retention period"
    + " is read first and put back, so a database that kept readings for a"
    + " year still does afterwards."
    + "\n\n"
    + "The admin token is left alone: a token belongs to the instance, not"
    + " to a database, so sensors on other machines keep writing."
    + "\n\n"
    + "[bold]Examples[/bold]\n\n"
    + "    labmon reset-database\n"
    + "    labmon reset-database --database rig-2 --yes"
)

Hard = Annotated[
    bool,
    typer.Option(
        "--hard",
        help="Reclaim the disk space now, instead of leaving a recoverable"
        + " copy the server clears in its own time",
    ),
]

Yes = Annotated[
    bool,
    typer.Option(
        "--yes",
        "-y",
        help="Skip the confirmation prompt, for a script that means it",
    ),
]


def reset_database(
    database: Database = None,
    hard: Hard = False,
    yes: Yes = False,
    log_level: LogLevelOption = "INFO",  # pyright: ignore[reportArgumentType]
) -> None:
    """Drop and recreate the database, keeping its retention."""
    configure(log_level)
    # Imported here, not at module scope: `labmon.influx` pulls in the
    # InfluxDB client, and `test_the_command_line_loads_without_the_heavy_libraries`
    # holds every `labmon --help` to not paying for it.
    from labmon.influx import AUTH_TOKEN, auth_token, influx_database, influx_host

    host = influx_host()
    name = database or influx_database()

    token = auth_token()
    if token is None:
        logger.error(
            "no admin token",
            extra={"reason": f"{AUTH_TOKEN} is unset; run `labmon init` first"},
        )
        raise SystemExit(REFUSED)

    if not admin.database_exists(host, token, name):
        # Creating it here would be a surprising thing for a command whose
        # name says reset, and the usual cause is a typo in --database.
        logger.error(
            "no such database",
            extra={"database": name, "host": host, "reason": "nothing to reset"},
        )
        raise SystemExit(REFUSED)

    # Read before the delete, because afterwards there is nothing to read
    # it from. Without this a reset silently returns a database that kept
    # a year of readings to keeping them for ever.
    retention = admin.read_retention(host, token, name)

    if not yes:
        # The name typed back rather than a y/n. This destroys readings
        # that cannot be recovered, and the mistake worth catching is
        # resetting the right way round on the wrong database.
        typed = cast(
            str,
            typer.prompt(
                f"This deletes every reading in {name!r} on {host}."
                + "\nType the database name to confirm"
            ),
        )
        if typed != name:
            logger.info("not confirmed; nothing was deleted", extra={"database": name})
            raise SystemExit(REFUSED)

    admin.delete_database(host, token, name, hard=hard)
    _ = admin.create_database(host, token, name, retention)
    logger.info(
        "reset the database",
        extra={
            "database": name,
            "retention": retention or "unlimited",
            # Named because the two differ in what can be undone, and the
            # difference is invisible from the command line afterwards.
            "deleted": "hard" if hard else "soft",
        },
    )
