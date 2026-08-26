"""The user's configuration file, and what it is allowed to say.

Distinct from the settings in `docs/configuration.md`, which describe a
deployment: a host, a database, a token. Those belong to the machine and
arrive through the environment, because that is how a container is
configured. This file belongs to the *person* — how they want readings
shown to them — so it lives beside their other dotfiles and is read from
`$XDG_CONFIG_HOME/labmon/labmon.toml`.

TOML rather than YAML: `tomllib` is in the standard library, calibration
files are already TOML, and a second format would mean a second parser
to document and a runtime dependency for a file most people never write.

Unknown keys are refused rather than ignored. A config file that quietly
skips what it does not recognise is the worst kind — a mistyped setting
looks applied, behaves as though it were absent, and gives nothing to
notice. The cost is that a key from a newer labmon fails on an older
one, which is a plain message rather than a silent wrong answer.

Imports nothing heavy: every command reads this, including the ones that
never touch a database.
"""

import logging
import os
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger: logging.Logger = logging.getLogger(__name__)

CONFIG_NAME = "labmon.toml"

# `timezone = "local"` means whatever the machine is set to, resolved
# once here rather than left for each caller to remember.
LOCAL = "local"


class ConfigError(Exception):
    """The configuration file exists and cannot be used as written.

    Carries the path, because the first question on reading one of these
    is always which file is meant — a per-procedure layout passed on the
    command line and the machine-wide file are both plausible.
    """


@dataclass(frozen=True)
class Config:
    """Everything the configuration file settles.

    One field today. It is a dataclass rather than a bare timezone so
    that adding the monitor's layout does not change every signature
    that carries the configuration around.
    """

    timezone: tzinfo = UTC


def config_path() -> Path:
    """Where the configuration lives, honouring `XDG_CONFIG_HOME`.

    Resolved the same way as the roster's cache path rather than through
    a dependency: two short functions are cheaper than a package, and
    they cannot disagree about what an unset variable means.
    """
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path(os.path.expanduser("~")) / ".config"
    return root / "labmon" / CONFIG_NAME


def load(path: Path | None = None) -> Config:
    """Read the configuration, treating a missing file as the defaults.

    Not having written one is the overwhelmingly common case and has to
    be ordinary. A file that *is* there and cannot be used is the
    opposite: it was written on purpose, so failing to apply it silently
    would defeat the reason it exists.
    """
    where = path if path is not None else config_path()
    try:
        raw = where.read_bytes()
    except FileNotFoundError:
        logger.debug("no configuration file", extra={"path": str(where)})
        return Config()
    except OSError as error:
        raise ConfigError(f"{where}: cannot be read ({error.strerror})") from None

    try:
        document = cast(dict[str, object], tomllib.loads(raw.decode()))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{where}: not valid TOML ({error})") from None

    return _from_document(document, where)


def _from_document(document: dict[str, object], where: Path) -> Config:
    """Build a `Config` from a parsed document, or say what is wrong."""
    unknown = sorted(set(document) - {"timezone"})
    if unknown:
        raise ConfigError(
            f"{where}: unknown setting{'s' if len(unknown) > 1 else ''}"
            + f" {', '.join(repr(name) for name in unknown)}"
        )

    zone = document.get("timezone", "UTC")
    if not isinstance(zone, str):
        raise ConfigError(
            f"{where}: timezone must be a string, not {type(zone).__name__}"
        )
    return Config(timezone=_resolve(zone, where))


def _resolve(name: str, where: Path) -> tzinfo:
    """A zone name as something `astimezone` accepts.

    `local` is resolved by asking the machine what an aware timestamp
    becomes, which is the same question `datetime.astimezone()` answers
    and needs no separate source of truth for the system zone.
    """
    if name in {"UTC", "utc"}:
        # Answered without `ZoneInfo`, which needs the system timezone
        # database — absent from some slim images. UTC is the default,
        # so the default must not depend on a package being installed.
        return UTC
    if name == LOCAL:
        local = datetime.now(UTC).astimezone().tzinfo
        if local is None:  # pragma: no cover - astimezone always sets one
            return UTC
        return local
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ConfigError(
            f"{where}: {name!r} is not a known timezone."
            + " Use an IANA name such as 'Europe/Paris', 'UTC', or 'local'."
            + " A name that looks right but is not found means the system"
            + " timezone database is missing; install 'tzdata'"
        ) from error
