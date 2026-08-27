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
from collections.abc import Sequence
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


# How a tile may be told to write its number. `auto` is the reading
# exactly as stored, its notation chosen by magnitude; `plain` and
# `scientific` force one or the other for a sensor the rule reads badly
# for. None of the three rounds anything away — a reading is shortened
# only by an explicit `precision`.
FORMATS: frozenset[str] = frozenset({"auto", "plain", "scientific"})

# What `labmon monitor` does when the file says nothing. Two seconds is
# fast enough that a value looks live and slow enough that a 14 ms query
# is nowhere near the duty cycle; fifteen minutes of history is enough
# for a trend without being enough to wait for.
DEFAULT_REFRESH = "2s"
DEFAULT_WINDOW = "15m"


@dataclass(frozen=True)
class Display:
    """How one sensor's readings should be written, wherever they appear.

    Separate from `Panel` because the two answer different questions. A
    panel says *put this on screen*, and only tiles have one; a display
    rule says *this is how many digits this instrument is worth*, which
    is true of a sensor whether or not anybody gave it a tile.

    It exists because the automatic rule cannot always be right. The
    panel quotes a reading at the precision the spread of its own window
    justifies, which is the right default — but the spread measures how
    far a quantity *moved*, not how well it was measured. A beam
    wandering 19 µm across a half-hour window has its position rounded
    to whole µm, and the reading itself is good to far better than that.
    Naming a precision is how somebody says so.
    """

    sensor_id: str
    measurement: str | None = None
    precision: int | None = None
    format: str = "auto"


def display_for(
    rules: "Sequence[Display]", sensor_id: str, measurement: str
) -> Display | None:
    """The rule that governs this reading, or `None` when none does.

    A rule naming the measurement wins over one that does not, so a
    probe reporting both a temperature and a pressure can be given
    different precisions without either rule having to exclude the
    other.
    """
    named = [
        rule
        for rule in rules
        if rule.sensor_id == sensor_id and rule.measurement == measurement
    ]
    if named:
        return named[0]
    loose = [
        rule
        for rule in rules
        if rule.sensor_id == sensor_id and rule.measurement is None
    ]
    return loose[0] if loose else None


@dataclass(frozen=True)
class Panel:
    """One tile in the panel's layout.

    Only `sensor_id` is required. Everything else refines how the tile
    reads, and a layout that names nothing but sensors is a perfectly
    good layout.

    `measurement` is optional because most sensors write to one table.
    Naming it is how a sensor that writes to two — a probe reporting
    both a temperature and a pressure — says which of them this tile is
    for. The tile always shows the measurement it settled on, so what is
    on screen is never ambiguous even when the configuration was.
    """

    sensor_id: str
    measurement: str | None = None
    title: str | None = None
    precision: int | None = None
    format: str = "auto"
    warn_above: float | None = None
    warn_below: float | None = None

    @property
    def heading(self) -> str:
        """What to write above the value."""
        return self.title or self.sensor_id


@dataclass(frozen=True)
class Monitor:
    """How the terminal panel behaves.

    `refresh` is seconds, already parsed: the file spells it "2s" the
    way `--since` is spelled, and resolving it here means a bad value is
    caught on load rather than on the first tick, after the panel has
    drawn itself over the terminal.

    `window` stays as written, because that is what the selection layer
    takes. It is parsed on load all the same, and the result thrown
    away, so a typo cannot wait for a refresh to surface.

    `panels` is a layout, kept in the order it was written. Sorting it
    would discard the one thing a layout says: which tile to look at
    first. Empty means the fallback table.

    `sensors` is a set of display rules, unordered — they are looked up
    by sensor, never walked. They apply to the panel's table and to its
    tiles alike, and a tile that names its own precision overrides the
    rule for that tile only.
    """

    refresh: float = 2.0
    window: str = DEFAULT_WINDOW
    panels: tuple[Panel, ...] = ()
    sensors: tuple[Display, ...] = ()


@dataclass(frozen=True)
class Config:
    """Everything the configuration file settles.

    A dataclass rather than a bare timezone so that adding a section
    does not change every signature that carries the configuration
    around.
    """

    timezone: tzinfo = UTC
    monitor: Monitor = Monitor()


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
    unknown = sorted(set(document) - {"timezone", "monitor"})
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
    return Config(
        timezone=_resolve(zone, where),
        monitor=_monitor(document.get("monitor", {}), where),
    )


def load_monitor(path: Path) -> Monitor:
    """Read a layout file passed with `--config`.

    The same shape as the `[monitor]` section, minus the prefix — a
    per-procedure layout is the same kind of thing as the one in the
    user file, and a second schema would be a second thing to document
    and a second parser to keep in step.

    A missing file is an error here, unlike the user configuration. This
    one was named on the command line, so its absence is a typo rather
    than the ordinary case.
    """
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ConfigError(f"{path}: cannot be read ({error.strerror})") from None

    try:
        document = cast(dict[str, object], tomllib.loads(raw.decode()))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{path}: not valid TOML ({error})") from None

    return _monitor(document, path)


def _monitor(section: object, where: Path) -> Monitor:
    """The `[monitor]` table, with every value checked on the way in."""
    if not isinstance(section, dict):
        raise ConfigError(
            f"{where}: monitor must be a table written as [monitor],"
            + f" not {type(section).__name__}"
        )
    table = cast(dict[str, object], section)

    unknown = sorted(set(table) - {"refresh", "window", "panels", "sensors"})
    if unknown:
        raise ConfigError(
            f"{where}: unknown monitor setting{'s' if len(unknown) > 1 else ''}"
            + f" {', '.join(repr(name) for name in unknown)}"
        )

    from labmon.export.window import WindowError, parse_duration, parse_instant

    refresh = table.get("refresh", DEFAULT_REFRESH)
    if not isinstance(refresh, str):
        raise ConfigError(
            f"{where}: monitor.refresh must be a duration in quotes such as"
            + f' "2s", not {type(refresh).__name__}'
        )
    try:
        seconds = parse_duration(refresh)
    except WindowError as error:
        raise ConfigError(f"{where}: monitor.refresh — {error}") from None
    if seconds <= 0:
        raise ConfigError(
            f"{where}: monitor.refresh must be greater than zero;"
            + " every zero seconds is a busy loop, not a fast panel"
        )

    window = table.get("window", DEFAULT_WINDOW)
    if not isinstance(window, str):
        raise ConfigError(
            f'{where}: monitor.window must be a string such as "15m",'
            + f" not {type(window).__name__}"
        )
    try:
        # Parsed and thrown away. The selection layer wants the text, but
        # a typo caught here beats one that surfaces on the first tick.
        _ = parse_instant(window)
    except WindowError as error:
        raise ConfigError(f"{where}: monitor.window — {error}") from None

    return Monitor(
        refresh=seconds,
        window=window,
        panels=_panels(table, where),
        sensors=_sensors(table, where),
    )


# What a `[[monitor.panels]]` entry may say. Anything else is refused,
# for the same reason an unknown top-level key is.
_PANEL_FIELDS: frozenset[str] = frozenset(
    {
        "sensor_id",
        "measurement",
        "title",
        "precision",
        "format",
        "warn_above",
        "warn_below",
    }
)


def _panels(table: dict[str, object], where: Path) -> tuple[Panel, ...]:
    """The `[[monitor.panels]]` array, in the order it was written."""
    listed = table.get("panels", [])
    if not isinstance(listed, list):
        raise ConfigError(
            f"{where}: monitor.panels must be a list of tables written as"
            + f" [[monitor.panels]], not {type(listed).__name__}"
        )

    panels: list[Panel] = []
    for position, entry in enumerate(cast(list[object], listed), start=1):
        panels.append(_panel(entry, position, where))
    return tuple(panels)


def _panel(entry: object, position: int, where: Path) -> Panel:
    """One tile's settings, named by position when something is wrong.

    "a panel is missing sensor_id" is unhelpful in a file with nine of
    them, and a layout is exactly the kind of file that grows to nine.

    Each field is checked by name rather than through a table of types.
    The table was shorter and could not be type-checked, and this is the
    boundary where untrusted text becomes a typed object — the one place
    where being explicit is worth the lines.
    """
    at = f"{where}: panel {position}"
    if not isinstance(entry, dict):
        raise ConfigError(f"{at} must be a table, not {type(entry).__name__}")
    fields = cast(dict[str, object], entry)

    unknown = sorted(set(fields) - _PANEL_FIELDS)
    if unknown:
        raise ConfigError(
            f"{at}: unknown setting{'s' if len(unknown) > 1 else ''}"
            + f" {', '.join(repr(name) for name in unknown)}"
        )

    sensor = fields.get("sensor_id")
    if not isinstance(sensor, str) or not sensor:
        raise ConfigError(
            f"{at} needs a sensor_id — a tile with no sensor has nothing to show"
        )

    precision = _optional_int(fields, "precision", at)
    if precision is not None and precision < 0:
        raise ConfigError(f"{at}: precision cannot be negative")

    style = _optional_str(fields, "format", at) or "auto"
    if style not in FORMATS:
        raise ConfigError(
            f"{at}: format must be one of {', '.join(sorted(FORMATS))}, not {style!r}"
        )

    return Panel(
        sensor_id=sensor,
        measurement=_optional_str(fields, "measurement", at),
        title=_optional_str(fields, "title", at),
        precision=precision,
        format=style,
        warn_above=_optional_float(fields, "warn_above", at),
        warn_below=_optional_float(fields, "warn_below", at),
    )


# What a `[[monitor.sensors]]` entry may say. Deliberately narrower than
# a panel's: a title and a threshold belong to a tile, and accepting
# them here would invite somebody to write an alarm that never fires.
_DISPLAY_FIELDS: frozenset[str] = frozenset(
    {"sensor_id", "measurement", "precision", "format"}
)


def _sensors(table: dict[str, object], where: Path) -> tuple[Display, ...]:
    """The `[[monitor.sensors]]` array of display rules."""
    listed = table.get("sensors", [])
    if not isinstance(listed, list):
        raise ConfigError(
            f"{where}: monitor.sensors must be a list of tables written as"
            + f" [[monitor.sensors]], not {type(listed).__name__}"
        )

    rules: list[Display] = []
    for position, entry in enumerate(cast(list[object], listed), start=1):
        rules.append(_display(entry, position, where))
    return tuple(rules)


def _display(entry: object, position: int, where: Path) -> Display:
    """One sensor's display rule, named by position when it is wrong."""
    at = f"{where}: monitor.sensors {position}"
    if not isinstance(entry, dict):
        raise ConfigError(f"{at} must be a table, not {type(entry).__name__}")
    fields = cast(dict[str, object], entry)

    unknown = sorted(set(fields) - _DISPLAY_FIELDS)
    if unknown:
        raise ConfigError(
            f"{at}: unknown setting{'s' if len(unknown) > 1 else ''}"
            + f" {', '.join(repr(name) for name in unknown)}"
        )

    sensor = fields.get("sensor_id")
    if not isinstance(sensor, str) or not sensor:
        raise ConfigError(
            f"{at} needs a sensor_id — a display rule for no sensor"
            + " governs nothing"
        )

    precision = _optional_int(fields, "precision", at)
    if precision is not None and precision < 0:
        raise ConfigError(f"{at}: precision cannot be negative")

    style = _optional_str(fields, "format", at) or "auto"
    if style not in FORMATS:
        raise ConfigError(
            f"{at}: format must be one of {', '.join(sorted(FORMATS))}, not {style!r}"
        )

    return Display(
        sensor_id=sensor,
        measurement=_optional_str(fields, "measurement", at),
        precision=precision,
        format=style,
    )


def _optional_str(fields: dict[str, object], name: str, at: str) -> str | None:
    given = fields.get(name)
    if given is None:
        return None
    if not isinstance(given, str):
        raise ConfigError(f"{at}: {name} must be a string, not {type(given).__name__}")
    return given


def _optional_int(fields: dict[str, object], name: str, at: str) -> int | None:
    given = fields.get(name)
    if given is None:
        return None
    # A bool is an int in Python and is never one of these.
    if isinstance(given, bool) or not isinstance(given, int):
        raise ConfigError(f"{at}: {name} must be a number, not {type(given).__name__}")
    return given


def _optional_float(fields: dict[str, object], name: str, at: str) -> float | None:
    given = fields.get(name)
    if given is None:
        return None
    # `warn_above = 80` is how anybody would write it, so an int counts.
    if isinstance(given, bool) or not isinstance(given, int | float):
        raise ConfigError(f"{at}: {name} must be a number, not {type(given).__name__}")
    return float(given)


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
