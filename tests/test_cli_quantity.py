"""Showing a reading at a magnitude a person can read."""

import pytest

from labmon.cli.quantity import (
    PLAIN_BELOW,
    PLAIN_FROM,
    for_display,
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


def test_a_reading_is_never_rounded_against_its_own_spread() -> None:
    # A deviation says how far a quantity moved while nobody was
    # looking, which is no statement about how well the instrument
    # measured it. Only the average is rounded against it.
    assert for_display(-7.441802197802218) == "-7.441802197802218"
    assert for_display(99.76556776556775) == "99.76556776556775"


def test_a_reading_that_is_not_finite_is_shown_as_it_is() -> None:
    # nan and inf have no precision to be displayed at, and both print
    # fine as they are.
    assert for_display(float("nan")) == "nan"
    assert for_display(float("inf"), precision=3) == "inf"


def test_a_precision_gives_exactly_that_many_decimals() -> None:
    # Including the trailing zeros: somebody who wrote `precision = 3`
    # is saying the instrument resolves that far.
    assert for_display(76.85, precision=3) == "76.850"


def test_a_forced_style_overrides_the_magnitude_rule() -> None:
    assert for_display(76.85, style="scientific") == "7.685e+01"
    assert for_display(2.765e14, style="plain") == "276500000000000.0"


def test_plain_is_plain_over_the_range_anyone_would_ask_for_it() -> None:
    # `repr` turns to scientific notation below 1e-4 and at 1e16, which
    # is most of the range where somebody would bother to write
    # `format = "plain"` at all — a gauge reading 5e-05 got the exponent
    # it had just asked not to have.
    assert for_display(5e-05, style="plain") == "0.00005"
    assert for_display(1.0215499803546169e-07, style="plain") == (
        "0.00000010215499803546169"
    )
    assert for_display(1e16, style="plain") == "10000000000000000"


def test_plain_still_rounds_nothing_away() -> None:
    # The notation changes, the value does not: every digit that
    # survived storage survives the rendering.
    for value in (5e-05, 1.0215499803546169e-07, 76.85, 2.765e14):
        assert float(for_display(value, style="plain")) == value


def test_no_instruction_at_all_falls_back_to_the_plain_rule() -> None:
    # No precision and no style: shown the way any other reading is.
    assert for_display(76.85) == "76.85"


# --------------------------------------------------------------------------
# An average is never quoted finer than its deviation
# --------------------------------------------------------------------------


def _place(text: str) -> int:
    """The decimal place of the last digit `text` actually prints.

    `Decimal` reads it off the written form rather than the value, which
    is the whole question here: `76.85` resolves to 1e-2 and `2.7e+05`
    to 1e+04, however close the two happen to be as numbers.
    """
    from decimal import Decimal

    return int(Decimal(text).as_tuple().exponent)


@pytest.mark.parametrize(
    ("mean", "sd"),
    [
        # Shapes taken from a running lab: a wavemeter at 276 THz, a
        # vacuum gauge, a cryostat, a beam wandering further than its
        # own reading, and room temperature.
        (276561299886778.84, 1534893.1913595975),
        (1.4720792215568853e-07, 2.732690203837357e-08),
        (3.8761035995250994e-08, 3.598960146214997e-08),
        (4.252718562874251, 0.17668588580785222),
        (-0.13543827463249214, 19.070777954794192),
        (20.78907185628743, 0.901774826760567),
        (95.55741999054412, 8.932080162400563),
    ],
)
def test_the_average_is_never_printed_finer_than_the_deviation(
    mean: float, sd: float
) -> None:
    # The two are rounded against each other, so the average can be
    # coarser — a reading smaller than its own spread is cut to a single
    # figure — but never finer, which would claim a precision the spread
    # denies. The one exception is the floor below, which only applies
    # where the spread is wider than the average it is quoted against.
    shown, deviation = quote(mean, sd)

    if abs(mean) < abs(sd):
        return
    assert _place(shown) >= _place(deviation)


def test_an_average_smaller_than_its_spread_is_allowed_to_be_coarser() -> None:
    # Cut to the one figure the floor keeps, which is far coarser than
    # the average's own seventeen digits and says the quantity is smaller
    # than the noise it was measured in.
    shown, _deviation = quote(0.0001, 0.019)

    assert shown == "1e-04"


def test_an_average_swamped_by_its_spread_keeps_one_figure() -> None:
    # Rounding it to the spread's place gives a bare `0`, which throws
    # away the sign and the magnitude both. The deviation beside it
    # already says how soft the number is.
    shown, deviation = quote(-0.13543827463249214, 19.070777954794192)

    assert shown == "-0.1"
    assert deviation == "19"
    assert _place(shown) < _place(deviation)


def test_a_precision_in_scientific_style_counts_significant_figures() -> None:
    # Decimal places are the wrong question for a vacuum gauge:
    # `precision = 2` on 1.02e-07 would ask for `0.00`.
    assert for_display(1.0215499803546169e-07, precision=2, style="scientific") == (
        "1.02e-07"
    )
    assert for_display(76.85, precision=3, style="scientific") == "7.685e+01"
