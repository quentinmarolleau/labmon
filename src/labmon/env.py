"""Read `.env` for a command typed at a prompt.

`.env` is read by Docker Compose, not by the shell. A token set there
reaches every container and no `labmon` anyone types, which is a
surprise nobody has to make twice: the file is right there, the
containers are visibly using it, and the command says the variable is
unset.

The repository ships an `.envrc` containing `dotenv`, so a machine with
direnv has never had the problem — which is exactly why it survived this
long. direnv is one more thing to install, and a monitoring tool should
not need it to read its own configuration.

Only entry points call this. A library that rewrote its importer's
environment would be a nasty thing to depend on.
"""

import logging
import os
from pathlib import Path

from dotenv import dotenv_values

logger: logging.Logger = logging.getLogger(__name__)

# The name Compose looks for, so both read the same file. Spelled once
# here because the "no auth token" message names it too.
ENV_FILE = ".env"


def load(directory: Path | None = None) -> Path | None:
    """Fill in settings from `./.env`, returning the file it read.

    The process environment wins over the file. A container, a systemd
    unit and a `VAR=x labmon …` prefix all set their variables
    deliberately, and none of them should start behaving differently
    because a file happens to sit in the working directory — so `.env`
    stays a convenience, not a second source of truth.

    Only the working directory is searched. Walking up would let a
    `.env` three levels above, belonging to something else entirely,
    configure a sensor; that surprise is worth more than the
    convenience.

    Parsing is `python-dotenv`'s rather than ours, because Compose reads
    this same file and the two have to agree on quoting. A deployment's
    `LABMON_LOKI_PUSH_HASH` is a bcrypt hash in single quotes, full of
    `$`, and a hand-rolled reader that expanded those would hand back a
    credential that silently does not match.
    """
    path = (directory or Path.cwd()) / ENV_FILE
    if not path.is_file():
        return None

    # `None` is what dotenv reports for a bare `KEY` line with no `=`.
    # Setting it would put an empty string in the environment, which
    # `influx._setting` treats as unset anyway, so the only thing it
    # could achieve is masking a value inherited from the parent.
    applied = {
        name: value
        for name, value in dotenv_values(path).items()
        if value is not None and name not in os.environ
    }
    os.environ.update(applied)

    # Announced when it changed something, and only then. That is
    # exactly when the quiet failure is possible — two checkouts side by
    # side, a command run in the wrong one, readings written to the
    # wrong database with nothing on screen to say which file decided
    # that. A file that supplied nothing decided nothing, and saying so
    # on every command would be noise for anyone whose shell already
    # exports these, which is everyone using the `.envrc` beside it.
    logger.log(
        logging.INFO if applied else logging.DEBUG,
        "read environment file",
        extra={"path": str(path), "applied": len(applied)},
    )
    return path
