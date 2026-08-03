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

# Characters that force a value to be quoted. A bare `=` would look like
# the start of another field, and whitespace would split one field in two.
_NEEDS_QUOTING = frozenset(' \t\n"=')


def quote(value: object) -> str:
    """Render a value as a logfmt token, quoting only when it must."""
    text = str(value)
    if text == "":
        return '""'
    if any(character in text for character in _NEEDS_QUOTING):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
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


def configure(level: int = logging.INFO) -> None:
    """Send this process's logs to stderr as logfmt.

    Called by each sensor's `main()`. A library should not configure
    logging on import, so nothing here runs until an entry point asks.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(LogfmtFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
