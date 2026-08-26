"""Showing a reading at a magnitude a person can read."""

import pytest

from labmon.cli.quantity import (
    PLAIN_BELOW,
    PLAIN_FROM,
    at_the_precision_of,
    quote,
    show,
)


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


# --------------------------------------------------------------------------
# A mean quoted against its own deviation
# --------------------------------------------------------------------------


def test_the_deviation_keeps_two_significant_figures() -> None:
    # The usual convention for a quoted spread. One figure throws away a
    # distinction that matters; more claims a precision the spread does
    # not have.
    _mean, sd = quote(20.928418855218855, 0.7816002878531931)

    assert sd == "0.78"


def test_the_mean_is_rounded_to_the_deviations_place() -> None:
    # Digits beyond the spread are noise. Printing them invites somebody
    # to read a difference that is not there.
    mean, _sd = quote(20.928418855218855, 0.7816002878531931)

    assert mean == "20.93"


def test_a_large_quantity_stays_scientific() -> None:
    mean, sd = quote(2.765612998548223e14, 1.9743483157749637e06)

    assert mean == "2.765612999e+14"
    assert sd == "2.0e+06"


def test_a_small_quantity_stays_scientific() -> None:
    mean, sd = quote(2.8789632980733558e-08, 3.3789931656056665e-08)

    assert mean == "2.9e-08"
    assert sd == "3.4e-08"


def test_rounding_does_not_leak_binary_error() -> None:
    # Rounding by dividing and multiplying back gives 2.3000000000000003
    # here, which is worse than the number it set out to shorten.
    _mean, sd = quote(-6.552811744779804e-05, 2.2515017720791146)

    assert sd == "2.3"


def test_a_mean_swamped_by_its_spread_still_says_where_it_sits() -> None:
    # A beam centred at 0.0196 with a spread of 16 rounds to a bare `0`
    # at the spread's place, which says the beam is somewhere within
    # +/-16 and nothing about where. One figure says both.
    mean, sd = quote(0.019626985717538424, 16.13679456395551)

    assert mean == "0.02"
    assert sd == "16"


def test_a_negative_mean_swamped_by_its_spread_keeps_its_sign() -> None:
    # Rounding this away gave `0` — and the sign, which is the one thing
    # a reader wants from a centring, went with it.
    mean, _sd = quote(-0.009321994508954552, 19.105288235209112)

    assert mean == "-0.009"


def test_the_floor_does_not_add_digits_a_tight_spread_already_allows() -> None:
    # The spread is far finer than two figures of the mean, so it stays
    # in charge and the mean keeps every digit that survives it.
    mean, _sd = quote(276.56130115, 3.1167749072745227e-07)

    assert mean == "276.56130115"


def test_the_floor_never_coarsens_a_mean() -> None:
    # One figure is a minimum, not a target: where the spread's place is
    # finer, the spread wins.
    mean, _sd = quote(20.928418855218855, 0.7816002878531931)

    assert mean == "20.93"


def test_a_swamped_mean_below_the_plain_range_stays_scientific() -> None:
    # The floor picks a decimal place; the magnitude still decides the
    # form the number is written in.
    mean, sd = quote(2.04e-05, 16.13679456395551)

    assert mean == "2e-05"
    assert sd == "16"


def test_a_mean_of_exactly_zero_has_no_figure_to_keep() -> None:
    # log10(0) has no answer, and a centring of exactly zero is already
    # the whole statement.
    mean, sd = quote(0.0, 16.13679456395551)

    assert mean == "0"
    assert sd == "16"


def test_a_tight_spread_keeps_the_digits_that_survive_it() -> None:
    # A wavemeter stable to 3e-07 has a mean good to eight decimals, and
    # rounding it to the usual few would throw the measurement away.
    mean, sd = quote(276.56130115, 3.1167749072745227e-07)

    assert mean == "276.56130115"
    assert sd == "3.1e-07"


def test_no_deviation_leaves_the_mean_as_it_was() -> None:
    # One reading in the window: nothing says where to round to, so the
    # value is shown the way any other reading would be.
    mean, sd = quote(21.0691, None)

    assert mean == "21.0691"
    assert sd == ""


def test_a_deviation_of_zero_is_not_a_place_to_round_to() -> None:
    # Every reading identical — a stuck sensor. log10(0) has no answer.
    mean, sd = quote(4.2, 0.0)

    assert mean == "4.2"
    assert sd == "0.0"


def test_a_flat_line_does_not_bloom_to_the_width_of_the_float() -> None:
    # `avg()` over identical readings need not give the reading back bit
    # for bit, and the stuck sensor is exactly the row being stared at.
    # Seventeen digits beside a deviation of `0.0` is the wrong answer to
    # the wrong question.
    mean, sd = quote(4.200000000000001, 0.0)

    assert mean == "4.2"
    assert sd == "0.0"


def test_a_flat_line_keeps_the_digits_a_measurement_could_carry() -> None:
    # The trim trades away float summation noise, not measurement. A
    # wavemeter reading is eleven significant digits and every one of
    # them survives.
    mean, _sd = quote(276.56130115, 0.0)

    assert mean == "276.56130115"


def test_a_deviation_that_is_not_finite_is_not_rounded_against() -> None:
    mean, sd = quote(4.2, float("nan"))

    assert mean == "4.2"
    assert sd == "nan"


def test_a_reading_can_be_shown_at_the_precision_of_its_spread() -> None:
    # A beam position whose spread over the window is 16 µm has two
    # meaningful characters, not nineteen.
    assert at_the_precision_of(-7.441802197802218, 16.13679456395551) == "-7"
    assert at_the_precision_of(99.76556776556775, 8.847037339748116) == "99.8"
    assert at_the_precision_of(76.923, 2.3) == "76.9"


def test_a_reading_with_nothing_to_round_against_says_so() -> None:
    # `None` rather than a guess, so the caller falls back to showing
    # the reading in full rather than inventing a precision.
    assert at_the_precision_of(76.923, None) is None
    assert at_the_precision_of(76.923, 0.0) is None
    assert at_the_precision_of(76.923, float("inf")) is None
    assert at_the_precision_of(float("nan"), 1.0) is None
