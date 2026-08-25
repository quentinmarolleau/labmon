"""Parsing the time window an export covers.

Two spellings are accepted wherever an instant is wanted, because the two
reasons for asking are different. An absolute timestamp answers "the run
on Tuesday afternoon"; a relative duration answers "whatever has happened
since I went home", which is the one people type repeatedly and would
otherwise have to recompute by hand every time.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# Suffixes a relative duration may carry, and what each is worth. Months
# and years are deliberately absent: they have no fixed length, so "1M"
# would have to pick a convention and would be wrong for somebody.
_UNIT_SECONDS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}

# Anchored with \Z so a partial match is not mistaken for a whole one:
# unanchored, `re.match` would accept "24hours" as "24h" and silently
# export a different window than the one asked for. Surrounding
# whitespace is stripped before this runs, so a value arriving from a
# shell substitution with a trailing newline is still accepted.
_DURATION = re.compile(r"(?P<amount>\d+(?:\.\d+)?)(?P<unit>[smhdw])\Z")


class WindowError(ValueError):
    """A --since or --until value that cannot be understood."""


def parse_instant(text: str, *, now: datetime | None = None) -> datetime:
    """Resolve one --since/--until value to an aware UTC datetime.

    Accepts an ISO 8601 date or timestamp ("2026-08-01",
    "2026-08-01T14:30:00+02:00") or a duration meaning that long ago
    ("24h", "90m", "7d").

    A timestamp with no offset is read as UTC rather than as local time.
    Local time would make the same command mean different things on two
    machines in the same lab, and readings are stored in UTC, so UTC is
    the interpretation that matches what comes back.
    """
    moment = now or datetime.now(UTC)
    candidate = text.strip()
    if not candidate:
        raise WindowError("a time bound cannot be empty")

    duration = _DURATION.match(candidate)
    if duration is not None:
        amount = float(duration.group("amount"))
        seconds = amount * _UNIT_SECONDS[duration.group("unit")]
        return moment - timedelta(seconds=seconds)

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise WindowError(
            f"{text!r} is neither an ISO 8601 timestamp (2026-08-01,"
            + " 2026-08-01T14:30:00+02:00) nor a duration (24h, 90m, 7d)"
        ) from error

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class Window:
    """The half-open interval [since, until) an export covers."""

    since: datetime
    until: datetime

    @classmethod
    def parse(
        cls,
        since: str | None,
        until: str | None,
        *,
        now: datetime | None = None,
    ) -> "Window":
        """Build a window from raw CLI strings, defaulting to the last hour.

        Both bounds resolve against a single `now`, so `--since 24h
        --until 1h` describes a fixed 23-hour span rather than two spans
        measured from slightly different instants.
        """
        moment = now or datetime.now(UTC)
        start = parse_instant(since or "1h", now=moment)
        end = parse_instant(until, now=moment) if until else moment
        if start >= end:
            raise WindowError(
                f"the window starts at {start.isoformat()} and ends at"
                + f" {end.isoformat()}, which selects nothing"
            )
        return cls(since=start, until=end)
