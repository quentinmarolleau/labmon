"""Dropping digits a reading was never entitled to."""

import math

import pytest

from labmon.quantise import to_resolution


def test_a_reading_is_rounded_to_the_place_its_step_reaches() -> None:
    # A 12-bit input scaled by 60 um/V resolves 0.0484 um, which is two
    # decimals' worth. Everything past them is conversion arithmetic.
    assert to_resolution(5.981234567890123, 0.0483516484) == 5.98


def test_a_reading_is_not_put_on_a_grid_of_whole_steps() -> None:
    # Stepping to 0.0483516484 would give 5.99560439 — longer than the
    # number it set out to shorten, and no more honest.
    rounded = to_resolution(5.981234567890123, 0.0483516484)

    assert len(repr(rounded)) < len("5.99560439")


@pytest.mark.parametrize(
    ("resolution", "expected"),
    [
        # 76.8 rather than 76.9 at tenths: halfway goes to even.
        (0.001, 76.85),
        (0.01, 76.85),
        (0.1, 76.8),
        (1.0, 77.0),
        (10.0, 80.0),
        (100.0, 100.0),
    ],
)
def test_a_coarser_step_keeps_fewer_places(resolution: float, expected: float) -> None:
    assert to_resolution(76.85, resolution) == expected


def test_the_step_is_read_at_its_leading_digit() -> None:
    # 0.5 and 0.1 reach the same place; a step of 0.5 cannot buy a
    # display of hundredths, and rounding to tenths never claims fewer
    # digits than the hardware has.
    assert to_resolution(76.85, 0.5) == to_resolution(76.85, 0.1)


def test_a_step_larger_than_the_reading_rounds_it_away() -> None:
    # Honest: the quantity is smaller than the smallest difference the
    # input can show.
    assert to_resolution(0.004, 0.5) == 0.0


def test_rounding_does_not_leak_binary_error() -> None:
    # Dividing and multiplying back gives 76.85000000000001 here.
    assert repr(to_resolution(76.85006139177405, 0.001)) == "76.85"


def test_halfway_goes_to_even() -> None:
    # Consistent with the rest of the stack, and unbiased over a series.
    assert to_resolution(0.125, 0.01) == 0.12
    assert to_resolution(0.135, 0.01) == 0.14


def test_a_reading_that_is_not_a_number_passes_through() -> None:
    # The loop refuses a non-finite value before it becomes a field;
    # rounding should not be the thing that raises first.
    assert math.isnan(to_resolution(float("nan"), 0.001))
    assert math.isinf(to_resolution(float("inf"), 0.001))


def test_a_step_beyond_the_decimal_context_leaves_the_reading_alone() -> None:
    # Some 1e30 below the reading, which nothing physical reaches. The
    # un-rounded value is the honest answer when the rounding itself is
    # meaningless.
    assert to_resolution(2.765613e14, 1e-30) == 2.765613e14


def test_a_scientific_magnitude_keeps_its_significant_digits() -> None:
    # A step of 1e-10 on a reading of 1.02e-07 leaves three of them.
    assert to_resolution(1.0215499803546169e-07, 1e-10) == 1.022e-07
