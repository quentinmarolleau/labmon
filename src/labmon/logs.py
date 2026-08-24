"""Emit logs as logfmt, so a collector can read them as fields.

Sensor output used to be free text — and in two of the four emitters,
bare `print()`, which carries no level at all. Grafana showed those lines
as `detected_level: unknown`, so "show me the warnings" was not a query
anyone could write.

logfmt fixes both halves. Every line carries its level, and the fields a
collector cares about — `sensor_id` above all — are named rather than
embedded in prose:

    ts=2026-08-04T09:31:07Z level=info logger=labmon.sensors.loop
      msg="wrote 30 readings in 30s" sensor_id=cryo-77k

Chosen over JSON because a human reads these too. A JSON line is machine
readable and miserable to skim in a terminal at three in the morning,
which is exactly when a sensor log gets read.

Fields come from `extra=`, so a call site names its data instead of
formatting it into a sentence:

    logger.warning("read failed", extra={"sensor_id": "laser-1"})
"""

import logging
from datetime import UTC, datetime
from typing import cast, override

# Attributes the logging module puts on every record. Anything else a
# caller passed through `extra=` is ours, and becomes a logfmt field.
_STANDARD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}

# Characters that force a value to be quoted because of what logfmt makes
# of them: a bare `=` looks like the start of another field, and a space
# splits one field in two.
_STRUCTURAL = frozenset(' "=')

# Escapes for the characters that have a conventional short spelling,
# which is what Go's logfmt writer and Python's own repr both use.
_SHORT_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _escaped(character: str) -> str:
    """One character, rendered so it cannot act on whatever reads it."""
    short = _SHORT_ESCAPES.get(character)
    if short is not None:
        return short
    if character.isprintable():
        return character
    # Anything else that draws rather than prints. `\r` is the obvious
    # case and has a short form above, but it is not the strongest one:
    # ESC introduces the ANSI sequences that repaint and reposition, BS
    # overwrites like `\r` does, and the bidi overrides reorder a line
    # without touching a single byte of its content.
    codepoint = ord(character)
    if codepoint < 0x100:
        return f"\\x{codepoint:02x}"
    if codepoint < 0x10000:
        return f"\\u{codepoint:04x}"
    return f"\\U{codepoint:08x}"


def _must_quote(text: str) -> bool:
    """Whether logfmt or a terminal would misread this value bare."""
    return any(
        character in _STRUCTURAL or not character.isprintable() for character in text
    )


def quote(value: object) -> str:
    """Render a value as a logfmt token, quoting only when it must.

    Quoting is not only about logfmt's own syntax. These values are not
    all ours — channel names arrive off the serial wire, and a malformed
    line is logged verbatim — so a value that a terminal would *act on*
    rather than print is quoted and escaped too, and one log line can no
    longer be made to display as something other than what it says.
    """
    text = str(value)
    if text == "":
        return '""'
    if _must_quote(text):
        return '"' + "".join(_escaped(character) for character in text) + '"'
    return text


class LogfmtFormatter(logging.Formatter):
    """Renders a record as `key=value` pairs, extras included."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat(
            timespec="milliseconds"
        )
        fields: list[tuple[str, object]] = [
            ("ts", timestamp),
            ("level", record.levelname.lower()),
            ("logger", record.name),
            ("msg", record.getMessage()),
        ]

        # `__dict__` is typed as holding Any; nothing here wants that,
        # since every value is about to become a string anyway.
        attributes = cast(dict[str, object], record.__dict__)
        extras = {
            key: value
            for key, value in attributes.items()
            if key not in _STANDARD_FIELDS
        }
        # Sorted so a given call site always renders its fields in the
        # same order, which makes the lines diffable and greppable.
        fields.extend(sorted(extras.items()))

        rendered = " ".join(f"{key}={quote(value)}" for key, value in fields)
        if record.exc_info:
            # The traceback goes after the fields rather than inside one:
            # it is multi-line by nature, and mangling it into a quoted
            # value would make it unreadable for the person who needs it.
            rendered = f"{rendered}\n{self.formatException(record.exc_info)}"
        return rendered


# The levels an entry point offers on its command line, in order of
# increasing severity. Spelled out so the CLI can reject anything else:
# resolving a level name with a default silently turns `--log-level DEGUB`
# into INFO, and the missing DEBUG lines then read as a code problem
# rather than as the typo they are.
LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def level_from_name(name: str) -> int:
    """Translate a `--log-level` value into a logging level.

    Raises KeyError on anything unknown, which is the point.
    """
    return logging.getLevelNamesMapping()[name.upper()]


def configure(level: int = logging.INFO) -> None:
    """Send this process's logs to stderr as logfmt.

    Called by each sensor's `main()`. A library should not configure
    logging on import, so nothing here runs until an entry point asks.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(LogfmtFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
