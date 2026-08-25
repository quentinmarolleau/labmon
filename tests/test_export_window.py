"""Parsing --since and --until."""

from datetime import UTC, datetime, timedelta

import pytest

from labmon.export.window import Window, WindowError, parse_instant

_NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30s", timedelta(seconds=30)),
        ("90m", timedelta(minutes=90)),
        ("24h", timedelta(hours=24)),
        ("7d", timedelta(days=7)),
        ("2w", timedelta(weeks=2)),
        ("1.5h", timedelta(minutes=90)),
    ],
)
def test_a_duration_counts_back_from_now(text: str, expected: timedelta) -> None:
    assert parse_instant(text, now=_NOW) == _NOW - expected


def test_a_bare_date_is_midnight_utc() -> None:
    assert parse_instant("2026-08-01", now=_NOW) == datetime(2026, 8, 1, tzinfo=UTC)


def test_a_timestamp_without_an_offset_is_read_as_utc() -> None:
    # Not as local time: the same command run on two machines in one lab
    # would otherwise select two different windows, and readings are
    # stored in UTC anyway.
    assert parse_instant("2026-08-01T14:30:00", now=_NOW) == datetime(
        2026, 8, 1, 14, 30, tzinfo=UTC
    )


def test_a_timestamp_with_an_offset_is_converted() -> None:
    assert parse_instant("2026-08-01T14:30:00+02:00", now=_NOW) == datetime(
        2026, 8, 1, 12, 30, tzinfo=UTC
    )


def test_surrounding_whitespace_is_ignored() -> None:
    assert parse_instant("  24h  ", now=_NOW) == _NOW - timedelta(hours=24)


@pytest.mark.parametrize("text", ["", "   "])
def test_an_empty_bound_is_refused(text: str) -> None:
    with pytest.raises(WindowError, match="cannot be empty"):
        _ = parse_instant(text, now=_NOW)


@pytest.mark.parametrize("text", ["24hours", "yesterday", "24", "1y"])
def test_an_unparsable_bound_names_both_spellings(text: str) -> None:
    with pytest.raises(WindowError, match="ISO 8601"):
        _ = parse_instant(text, now=_NOW)


def test_a_duration_with_a_trailing_newline_still_parses() -> None:
    # `--since "$(cat window.txt)"` is a reasonable thing to write, and
    # the newline it carries is not the user making a mistake.
    assert parse_instant("24h\n", now=_NOW) == _NOW - timedelta(hours=24)


def test_a_partial_duration_match_is_not_accepted() -> None:
    # The \Z anchor: unanchored, "24hours" matches "24h" and exports a
    # window nobody asked for.
    with pytest.raises(WindowError, match="ISO 8601"):
        _ = parse_instant("24hours", now=_NOW)


def test_parse_instant_defaults_to_the_current_time() -> None:
    before = datetime.now(UTC)

    parsed = parse_instant("0s")

    assert before <= parsed <= datetime.now(UTC)


def test_a_window_defaults_to_the_last_hour() -> None:
    window = Window.parse(None, None, now=_NOW)

    assert window.since == _NOW - timedelta(hours=1)
    assert window.until == _NOW


def test_both_bounds_resolve_against_one_instant() -> None:
    # --since 24h --until 1h has to describe a fixed 23-hour span, not two
    # spans measured from slightly different readings of the clock.
    window = Window.parse("24h", "1h", now=_NOW)

    assert window.until - window.since == timedelta(hours=23)


def test_a_window_that_selects_nothing_is_refused() -> None:
    with pytest.raises(WindowError, match="selects nothing"):
        _ = Window.parse("1h", "2h", now=_NOW)


def test_a_zero_length_window_is_refused() -> None:
    with pytest.raises(WindowError, match="selects nothing"):
        _ = Window.parse("1h", "1h", now=_NOW)


def test_window_parse_defaults_to_the_current_time() -> None:
    before = datetime.now(UTC)

    window = Window.parse("1s", None)

    assert before - timedelta(seconds=2) <= window.since <= datetime.now(UTC)
