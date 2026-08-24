"""Shared plumbing every command needs: logging, and reporting failures.

Typer has no place to hang a global exception handler, so the mapping
from an exception to an exit code lives here and `labmon.cli.main` wraps
the app in it. Each command calls `configure()` first so its own
`--log-level` takes effect before anything is logged.
"""

import functools
import logging
from collections.abc import Callable

from influxdb_client_3.exceptions.exceptions import InfluxDB3ClientError

from labmon import logs
from labmon.export.query import QueryError
from labmon.export.window import WindowError
from labmon.export.writers import ExportError
from labmon.influx import influx_host

logger: logging.Logger = logging.getLogger(__name__)

# Exit codes. 1 is left to unexpected failures and 2 to Typer's own usage
# errors, so a script can tell "you typed it wrong" from "the request was
# fine but could not be carried out" from "the database was not there".
REFUSED = 2
UNREACHABLE = 3


def configure(level: object) -> None:
    """Apply a command's --log-level. Accepts the enum or its value."""
    name = getattr(level, "value", level)
    logs.configure(logs.level_from_name(str(name)))


def _first_line(message: str) -> str:
    """The first sentence of a client error, without the gRPC dump."""
    return message.split(". gRPC")[0].strip()


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
    try:
        action()
    except (ExportError, QueryError, WindowError) as error:
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
            logger.error(
                "no auth token",
                extra={
                    "reason": "INFLUXDB3_AUTH_TOKEN is not set;"
                    + " see docs/configuration.md"
                },
            )
            raise SystemExit(UNREACHABLE) from None
        raise
