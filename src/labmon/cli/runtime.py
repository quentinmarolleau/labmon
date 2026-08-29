"""Shared plumbing every command needs: logging, and reporting failures.

Typer has no place to hang a global exception handler, so the mapping
from an exception to an exit code lives here and `labmon.cli.main` wraps
the app in it. Each command calls `configure()` first so its own
`--log-level` takes effect before anything is logged.
"""

import functools
import logging
from collections.abc import Callable
from pathlib import Path

from labmon import env, logs

logger: logging.Logger = logging.getLogger(__name__)

# Exit codes. 1 is left to unexpected failures and 2 to Typer's own usage
# errors, so a script can tell "you typed it wrong" from "the request was
# fine but could not be carried out" from "the database was not there".
REFUSED = 2
UNREACHABLE = 3


def configure(level: object) -> None:
    """Apply a command's --log-level, then read `./.env`.

    In that order, and as a command's first act. The level has to be in
    force before the line naming the file is emitted, and the file has
    to be read before any setting is looked up — every command calls
    this before it touches the environment, which is what makes one call
    site enough. Tab completion never reaches a command body, so it
    never pays for the read.

    Accepts the enum or its value.
    """
    name = getattr(level, "value", level)
    logs.configure(logs.level_from_name(str(name)))
    _ = env.load()


def _first_line(message: str) -> str:
    """The first sentence of a client error, without the gRPC dump."""
    return message.split(". gRPC")[0].strip()


def _no_token_reason() -> str:
    """Why the token is missing, in terms of what the reader can see.

    Naming the variable and pointing at the manual is what this said
    before, and it sent somebody who had set that variable — in `.env`,
    where the containers were visibly reading it — round three wrong
    turns. The useful fact is which file was in play, so the message
    distinguishes the two cases rather than describing the mechanism.
    """
    if (Path.cwd() / env.ENV_FILE).is_file():
        return (
            f"INFLUXDB3_AUTH_TOKEN is not set, and the {env.ENV_FILE} in this"
            + " directory does not define it either"
        )
    return (
        f"INFLUXDB3_AUTH_TOKEN is not set, and there is no {env.ENV_FILE} in"
        + f" this directory. {env.ENV_FILE} is read by Docker Compose rather"
        + " than by your shell, so run labmon where that file is, or export"
        + " it first with: set -a; . ./.env; set +a"
    )


def reporting[**P](command: Callable[P, None]) -> Callable[P, None]:
    """Wrap a command so the failures a user can cause become messages.

    Applied to each command as it is registered rather than around the
    whole app, so it holds however the command is reached — through the
    entry point, through a deprecated alias, or through Typer's
    CliRunner in a test. Wrapping only the entry point would leave the
    tests exercising a different error path from the real one.

    Everything caught here is a condition somebody can hit by typing a
    reasonable command at an unreasonable moment. A traceback for any of
    them buries the one fact that matters, so each becomes a single
    logged line and an exit code.
    """

    @functools.wraps(command)
    def reported(*args: P.args, **kwargs: P.kwargs) -> None:
        _report(lambda: command(*args, **kwargs))

    return reported


def _report(action: Callable[[], None]) -> None:
    # Imported here rather than at module scope. Between them the
    # InfluxDB client and the export package cost about 0.3s to load,
    # and this module is imported by every command including the ones
    # that never touch a database — and by tab completion, which never
    # reaches this function at all.
    from influxdb_client_3.exceptions.exceptions import InfluxDB3ClientError

    from labmon.config import ConfigError
    from labmon.export.query import QueryError
    from labmon.export.window import WindowError
    from labmon.export.writers import ExportError
    from labmon.influx import influx_host

    try:
        action()
    except (ConfigError, ExportError, QueryError, WindowError) as error:
        logger.error("command failed", extra={"reason": str(error)})
        raise SystemExit(REFUSED) from None
    except InfluxDB3ClientError as error:
        # A server that is down, unreachable, or refusing the token. The
        # client raises this with a gRPC dump attached; the host is named
        # instead, because the usual cause is a command run against the
        # wrong one.
        logger.error(
            "cannot reach the database",
            extra={"host": influx_host(), "reason": _first_line(str(error))},
        )
        raise SystemExit(UNREACHABLE) from None
    except KeyError as error:
        # get_client() raises this when INFLUXDB3_AUTH_TOKEN is unset,
        # which as a bare traceback names the variable and nothing else.
        if error.args and error.args[0] == "INFLUXDB3_AUTH_TOKEN":
            logger.error("no auth token", extra={"reason": _no_token_reason()})
            raise SystemExit(UNREACHABLE) from None
        raise
