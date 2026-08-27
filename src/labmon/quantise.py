"""Dropping digits a reading was never entitled to.

Floating point arithmetic hands back seventeen digits whatever went into
it. Writing all of them claims a precision no instrument has, and leaves
whoever opens the exported column unable to tell a physical millikelvin
from an artefact of the conversion.

Two ways of saying how far to keep, because instruments are specified
both ways: an absolute step, for a part with a fixed least-significant
digit, and a count of significant figures, for a quantity that may sit
anywhere on the scale.

Shared between the simulated sensor, which is simply told how precise to
pretend to be, and the calibrated one, which derives the answer from its
ADC and its conversion.
"""

import math
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from labmon.sensors import constants

# How many digits a reading carries when no absolute step is given.
# Significant digits rather than decimal places because a sensor may sit
# anywhere on the scale: the demo alone spans 4 K and 1.5e-7 mbar, and a
# fixed number of decimals reports one of those as zero.
DEFAULT_SIGNIFICANT_DIGITS = constants.DEFAULT_SIGNIFICANT_DIGITS


def quantise(
    value: float,
    resolution: float | None = None,
    significant_digits: int = DEFAULT_SIGNIFICANT_DIGITS,
) -> float:
    """Round a reading to the precision the instrument has.

    `resolution` is an absolute step in the reading's own units, which is
    how an instrument with a fixed least-significant digit is specified
    (a thermometer resolving 0.001 K). Without one, the value is rounded
    to `significant_digits` instead, which stays meaningful at any
    magnitude — the safe default, since an absolute step large enough for
    a thermometer reports a vacuum gauge as zero.

    Stepping is done in Decimal rather than as `round(value / step) *
    step`: the arithmetic form puts back the noise it is removing, giving
    76.85000000000001 where the decimal form gives 76.85.
    """
    if not math.isfinite(value):
        # `polling` refuses a non-finite value before it becomes a field;
        # rounding should not be the thing that raises first.
        return value
    if resolution is not None:
        step = Decimal(str(resolution))
        return float(
            (Decimal(repr(value)) / step).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
            * step
        )
    return float(f"{value:.{significant_digits}g}")


def to_resolution(value: float, resolution: float) -> float:
    """Round `value` to the decimal place `resolution` reaches.

    Unlike `quantise`, this does not put the reading on a grid of whole
    steps. A derived resolution is not a round number — a 12-bit input
    scaled by 60 µm/V resolves 0.0484 µm — and stepping to it produces
    5.99560439, which is longer than the number it set out to shorten
    and no more honest.

    What a person writes instead is the decimal place the step reaches:
    0.0484 µm means two decimals, so the reading is 5.98. That keeps at
    most one digit more than the hardware supports, which is how every
    instrument front panel is specified, and never keeps fewer.
    """
    if not math.isfinite(value):
        return value
    place = math.floor(math.log10(resolution))
    try:
        return float(
            Decimal(repr(value)).quantize(
                Decimal(1).scaleb(place), rounding=ROUND_HALF_EVEN
            )
        )
    except InvalidOperation:
        # Asking for more digits than the decimal context carries, which
        # needs a resolution some 1e28 below the reading. Nothing
        # physical gets there, and the un-rounded value is the honest
        # answer when the rounding itself is meaningless.
        return value
