"""Showing a reading at a magnitude a person can read."""

import pytest

from labmon.cli.quantity import PLAIN_BELOW, PLAIN_FROM, show


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, "0.0"),
        (4.2, "4.2"),
        (20.575, "20.575"),
        (999.9, "999.9"),
        (107.39021978021978, "107.39021978021978"),
        (-3.107, "-3.107"),
        (0.001, "0.001"),
    ],
)
def test_an_ordinary_magnitude_is_left_alone(value: float, expected: str) -> None:
    # Between a millilitre and a thousand, a plain decimal reads fine and
    # rewriting it would only add noise.
    assert show(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (276561300200000.0, "2.765613002e+14"),
        (1234.0, "1.234e+03"),
        (0.000023, "2.3e-05"),
        (6.84648e-07, "6.84648e-07"),
        (-1.5e-7, "-1.5e-07"),
    ],
)
def test_an_awkward_magnitude_becomes_scientific(value: float, expected: str) -> None:
    # More than three digits before the point, or zeros crowding after
    # it, is where a column of numbers stops being readable.
    assert show(value) == expected


@pytest.mark.parametrize(
    "value",
    [276561300200000.0, 0.000023, 6.84648e-07, 107.39021978021978, 1.5e-7, 4.2],
)
def test_nothing_is_rounded_away(value: float) -> None:
    # Sensors already round to the resolution they claim, so this shows
    # what was stored rather than inventing or hiding digits.
    assert float(show(value)) == value


def test_the_shortest_form_that_survives_is_the_one_shown() -> None:
    # 17 significant digits always round-trips and always looks awful.
    assert show(1.5e-7) == "1.5e-07"


def test_a_value_that_is_not_a_number_is_still_printable() -> None:
    assert show(float("nan")) == "nan"
    assert show(float("inf")) == "inf"


def test_the_boundaries_are_ordered() -> None:
    assert 0 < PLAIN_FROM < PLAIN_BELOW
