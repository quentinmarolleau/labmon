"""How long ago a reading arrived, said briefly and coloured by concern."""

from datetime import timedelta

import pytest

from labmon.cli.age import FRESH_UNTIL, STALE_AFTER, Freshness, describe, freshness


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0s ago"),
        (1, "1s ago"),
        (59, "59s ago"),
        (60, "1m ago"),
        (3599, "59m ago"),
        (3600, "1h ago"),
        (86_399, "23h ago"),
        (86_400, "1d ago"),
        (86_400 * 9, "9d ago"),
    ],
)
def test_an_age_reads_as_one_number_and_one_unit(seconds: int, expected: str) -> None:
    # A glance has to answer "is this current?", which one significant
    # unit does and "1h 3m 12s" does not.
    assert describe(timedelta(seconds=seconds)) == expected


def test_a_reading_from_the_future_is_not_negative() -> None:
    # Clock skew between a sensor host and this one is ordinary, and
    # "-3s ago" reads as a bug in labmon rather than a clock to check.
    assert describe(timedelta(seconds=-3)) == "0s ago"


def test_a_recent_reading_is_fresh() -> None:
    assert freshness(FRESH_UNTIL - timedelta(seconds=1)) is Freshness.FRESH


def test_freshness_turns_at_the_documented_boundaries() -> None:
    assert freshness(FRESH_UNTIL) is Freshness.AGEING
    assert freshness(STALE_AFTER - timedelta(seconds=1)) is Freshness.AGEING
    assert freshness(STALE_AFTER) is Freshness.STALE


def test_the_boundaries_are_ordered() -> None:
    # A sensor sampling once a minute is normal here, so the fresh
    # window has to clear a whole interval before it starts warning.
    assert timedelta(0) < FRESH_UNTIL < STALE_AFTER
