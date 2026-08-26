"""How long ago a reading arrived, said briefly and coloured by concern.

Imports nothing beyond the standard library, so naming a threshold as a
CLI option default costs no startup time.
"""

from datetime import timedelta
from enum import Enum

# A sensor here may legitimately sample once a minute — the demo stack
# spans 1 Hz to that — so the fresh window has to clear a whole interval
# before it starts warning, or every slow sensor reads as a problem.
FRESH_UNTIL: timedelta = timedelta(minutes=1)

# Past this, silence is worth explaining rather than shrugging at. Both
# thresholds are deliberately global for now; a per-sensor expected
# interval belongs in the configuration file, and is tracked separately.
STALE_AFTER: timedelta = timedelta(minutes=5)

_UNITS: tuple[tuple[int, str], ...] = (
    (86_400, "d"),
    (3_600, "h"),
    (60, "m"),
    (1, "s"),
)


class Freshness(Enum):
    """How much concern an age deserves at a glance."""

    FRESH = "fresh"
    AGEING = "ageing"
    STALE = "stale"


def describe(delta: timedelta) -> str:
    """`delta` as one number and one unit, e.g. `3m ago`.

    One significant unit rather than `1h 3m 12s`: the question being
    asked of this column is "is this current?", and a single magnitude
    answers it where a breakdown has to be read.

    A negative age is reported as zero. Clock skew between a sensor host
    and this one is ordinary, and `-3s ago` reads as a bug in labmon
    rather than as a clock worth checking.
    """
    seconds = max(0, int(delta.total_seconds()))
    for size, suffix in _UNITS:
        if seconds >= size:
            return f"{seconds // size}{suffix} ago"
    return "0s ago"


def freshness(delta: timedelta) -> Freshness:
    """Which band `delta` falls in, for colouring."""
    if delta < FRESH_UNTIL:
        return Freshness.FRESH
    if delta < STALE_AFTER:
        return Freshness.AGEING
    return Freshness.STALE
