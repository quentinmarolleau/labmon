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


# Significant figures kept on a standard deviation. Two is the usual
# convention: one throws away a distinction that matters between 0.11
# and 0.19, and three claims a precision a spread does not have.
DEVIATION_FIGURES = 2

# Significant figures a mean keeps however wide its spread. A spread
# wider than the mean rounds it to a bare `0`, which states the one
# thing already known — that the centre lies somewhere inside the
# spread — and discards the centring itself, sign included. One figure
# is enough to recover both; a second would only sharpen a number the
# spread has already said is soft.
MEAN_FIGURES = 1

# Significant digits a mean keeps when its deviation is exactly zero.
# Every reading in the window was identical, so the only thing between
# `avg()` and that reading is the error of the sum. A float64 carries
# close to sixteen digits and averaging spends a few of them, so twelve
# keeps every digit a measurement could have while dropping the
# arithmetic's own leavings.
IDENTICAL_DIGITS = 12


def quote(mean: float, sd: float | None) -> tuple[str, str]:
    """A mean and its standard deviation, rounded against each other.

    A stored reading is shown by `show` with nothing rounded away,
    because the sensor already rounded it to the resolution it claims
    and dropping digits would hide what was recorded. A mean is not a
    stored reading. It is computed, so its shortest round-tripping form
    runs to seventeen digits — `76.98545095398427` for readings that
    were only ever good to four — and printing that invites somebody to
    read a difference the measurement cannot support.

    So the deviation is cut to two significant figures, and the mean is
    cut to the same decimal place — but never past one figure of its
    own. A wavemeter stable to 3e-07 keeps eleven digits; a beam centred
    at 0.0196 with a spread of 16 reads as 0.02, holding on to a
    centring the spread would otherwise have rounded away.

    Rounded through `Decimal` rather than by dividing and multiplying
    back: the float route turns 2.2515 into 2.3000000000000003, which is
    worse than the number it set out to shorten.

    Returns both as text, since neither can be rounded without the
    other. An absent deviation — one reading in the window, so there is
    nothing to round against — leaves the mean exactly as `show` would
    give it.
    """
    if sd is None:
        return show(mean), ""
    if not math.isfinite(sd) or not math.isfinite(mean):
        return show(mean), show(sd)
    if sd == 0.0:
        # A flat line, which is a stuck sensor and so the row most
        # likely to be stared at. There is no spread to round against,
        # but `avg()` over identical readings is not obliged to give the
        # reading back exactly, and the difference is the sum's rather
        # than the instrument's.
        return show(float(f"{mean:.{IDENTICAL_DIGITS}g}")), show(sd)

    # The decimal place of the deviation's last significant figure.
    place = math.floor(math.log10(abs(sd))) - (DEVIATION_FIGURES - 1)
    return _at_place(mean, _for_mean(mean, place)), _at_place(sd, place)


def _for_mean(mean: float, place: int) -> int:
    """`place`, or finer where rounding there would leave the mean bare.

    One figure is a floor rather than a target, so a spread finer than
    the mean stays in charge and the digits it supports are all kept. It
    only bites the other way round, where the spread is the wider of the
    two and would otherwise take the whole mean with it.

    A mean of exactly zero has no significant figures to keep and no
    logarithm to find them with, and is already the entire statement.
    """
    if mean == 0.0:
        return place
    return min(place, math.floor(math.log10(abs(mean))) - (MEAN_FIGURES - 1))


def at_the_precision_of(value: float, sd: float | None) -> str | None:
    """`value` rounded to where `sd` says its digits stop meaning anything.

    For a panel read at a glance rather than a table read carefully.
    `-7.441802197802218 µm` on a beam whose spread over the window is
    16 µm is nineteen characters of which two carry information, and it
    crowds out the rest of the row.

    Returns `None` when there is nothing to round against, so the caller
    can fall back to showing the reading in full.
    """
    if sd is None or not math.isfinite(sd) or sd == 0.0 or not math.isfinite(value):
        return None
    place = math.floor(math.log10(abs(sd))) - (DEVIATION_FIGURES - 1)
    return _at_place(value, place)


def _at_place(value: float, place: int) -> str:
    """`value` rounded to `10 ** place`, plain or scientific to match `show`.

    `Decimal(repr(value))` rather than `Decimal(value)`: the latter
    expands the binary float to its exact decimal value, tens of digits
    of which are artefacts of the representation rather than of the
    measurement.
    """
    from decimal import Decimal

    quantised = Decimal(repr(value)).quantize(Decimal(1).scaleb(place))
    if quantised == 0:
        # Only a mean of exactly zero reaches this, since every other
        # value keeps a figure of itself. `-0` is the same statement
        # with a sign nobody wants to read.
        return "0"

    magnitude = abs(float(quantised))
    if PLAIN_FROM <= magnitude < PLAIN_BELOW:
        return f"{quantised:f}"

    # Digits after the point in scientific form: how far the leading
    # digit sits above the place being rounded to.
    leading = math.floor(math.log10(magnitude))
    return f"{float(quantised):.{max(0, leading - place)}e}"
