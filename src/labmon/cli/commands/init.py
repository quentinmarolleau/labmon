"""`labmon init` — issue the admin token and create the database.

The step this replaces was `docker compose exec influxdb influxdb3
create token --admin`, followed by copying the token out of the terminal
and into `.env` by hand. That works on the machine running the stack and
nowhere else: a sensor machine across the lab has no container to exec
into. The token endpoint is served over HTTP like everything else, so
there was never a reason for the container to be involved.
"""

import logging
from typing import Annotated

import typer

from labmon import admin, env
from labmon.cli.options import LogLevelOption
from labmon.cli.runtime import REFUSED, configure

logger: logging.Logger = logging.getLogger(__name__)

HELP = (
    "Prepare a fresh InfluxDB instance, and write its token to"
    + " [bold].env[/bold]."
    + "\n\n"
    + "Issues the instance's admin token, saves it, and creates the"
    + " database. Safe to run twice: the server refuses a second token and"
    + " a second database of the same name, so a re-run reports what is"
    + " already there and changes only the retention, if you ask for one."
    + "\n\n"
    + "[bold]Examples[/bold]\n\n"
    + "    labmon init\n"
    + "    labmon init --retention 1y\n"
    + "    labmon init --database rig-2 --retention 90d"
)

Retention = Annotated[
    str | None,
    typer.Option(
        "--retention",
        help="How long to keep readings, e.g. 1y, 90d, 24h. Left unset they"
        + " are kept for ever, which is what a database created by a write"
        + " gets",
        metavar="DURATION",
        show_default=False,
    ),
]

Database = Annotated[
    str | None,
    typer.Option(
        "--database",
        "-d",
        help="Database to create; defaults to INFLUXDB_DATABASE, then 'lab'",
        metavar="NAME",
        show_default=False,
    ),
]


def init(
    retention: Retention = None,
    database: Database = None,
    log_level: LogLevelOption = "INFO",  # pyright: ignore[reportArgumentType]
) -> None:
    """Bootstrap the instance, then say what is now true of it."""
    configure(log_level)
    # Imported here, not at module scope: `labmon.influx` pulls in the
    # InfluxDB client, and `test_the_command_line_loads_without_the_heavy_libraries`
    # holds every `labmon --help` to not paying for it.
    from labmon.influx import AUTH_TOKEN, auth_token, influx_database, influx_host

    host = influx_host()
    name = database or influx_database()

    issued = admin.create_admin_token(host)
    if issued is not None:
        _ = env.write(AUTH_TOKEN, issued)
        logger.info(
            "issued an admin token and saved it",
            extra={"host": host, "file": env.ENV_FILE},
        )
    else:
        logger.info("instance already has an admin token", extra={"host": host})

    token = issued or auth_token()
    if token is None:
        # The server issues the admin token once and keeps no copy it will
        # hand back, so there is nothing to recover — only a machine that
        # already has it, or a regeneration that costs every other client.
        logger.error(
            "this instance is initialised, but no token is configured here",
            extra={
                "reason": f"{AUTH_TOKEN} is unset and {env.ENV_FILE} does not"
                + " define it. An admin token is shown once and cannot be read"
                + " back, so copy it from a machine that has it. Regenerating"
                + " it with `influxdb3 create token --admin --regenerate`"
                + " invalidates every client's copy, including every sensor"
            },
        )
        raise SystemExit(REFUSED)

    if admin.create_database(host, token, name, retention):
        logger.info(
            "created the database",
            extra={"database": name, "retention": retention or "unlimited"},
        )
        return

    logger.info("database already exists", extra={"database": name})
    if retention is None:
        return
    # The one thing a re-run is allowed to change. Creating is refused
    # when the database is there, but the retention is a property of a
    # database rather than of its creation, so it stays adjustable.
    admin.set_retention(host, token, name, retention)
    logger.info("set the retention", extra={"database": name, "retention": retention})
