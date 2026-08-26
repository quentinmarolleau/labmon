"""Showing a reading at a magnitude a person can read.

A column of readings stops being readable at two places: when there are
more than three digits ahead of the decimal point, and when zeros crowd
in behind it. `276561300200000.0` and `0.00000068` both have to be
counted rather than read, and counting is exactly what a glance cannot
do.

Scientific notation rather than an SI prefix, deliberately. Rescaling
`Hz` to `THz` is the prettiest answer for a wavemeter and the wrong one
almost everywhere else in a lab: `mbar` already carries a prefix, so
"choose the right prefix" turns a vacuum reading into picobar, which is
correct and unreadable to anybody who works with vacuum. `°C` is an
offset unit and cannot be scaled at all — `pint` raises rather than
guess. Scientific notation touches neither the unit nor the physics, so
it works for every quantity a sensor writes.

A per-sensor prefix, chosen rather than inferred, is the better answer
for the cases where a prefix is wanted. That is a display preference,
and it belongs in the configuration file rather than in a rule applied
to every reading alike.

Imports nothing, so naming a boundary as a CLI option default costs no
startup time.
"""

import math

# Below this, zeros crowd in after the point: 0.00023 has to be counted.
PLAIN_FROM = 1e-3

# From this up, more than three digits sit ahead of the point, which is
# the same problem at the other end: 1234.0 has to be counted too.
PLAIN_BELOW = 1e3

# A float needs at most 17 significant digits to survive a round trip.
_MAX_DIGITS = 17


def show(value: float) -> str:
    """`value` as text, plain where that reads well and scientific where
    it does not.

    Nothing is rounded away in either form. Sensors already round to the
    resolution they claim, so a display that dropped digits would be
    hiding what was actually recorded — the shortest form that still
    reads back as the same float is used instead.
    """
    if not math.isfinite(value):
        # nan and inf have no magnitude to judge, and both print fine.
        return repr(value)

    magnitude = abs(value)
    if magnitude == 0.0 or PLAIN_FROM <= magnitude < PLAIN_BELOW:
        return repr(value)
    return _scientific(value)


def _scientific(value: float) -> str:
    """The shortest scientific form that reads back as `value`.

    `f"{value:e}"` fixes six digits, which both truncates a long reading
    and pads a short one — `1.5e-7` would print as `1.500000e-07`.
    Growing the precision until the text parses back to the same float
    gives the shortest honest answer instead.
    """
    text = ""
    for digits in range(_MAX_DIGITS):
        text = f"{value:.{digits}e}"
        if float(text) == value:
            break
    # The loop always leaves `text` set, and its last attempt carries 17
    # significant digits — which round-trips any float there is, so the
    # break is reached for every real value rather than fallen past.
    return text
