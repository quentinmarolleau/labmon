import logging

import pytest

from labmon.calibration import ureg
from labmon.gate import RecordingGate, RecordingRule


def _field(record: logging.LogRecord, name: str) -> object:
    """Read a logfmt field off a record.

    `extra=` fields become attributes a type checker cannot know about,
    so this says plainly that the lookup is dynamic.
    """
    return getattr(record, name)  # pyright: ignore[reportAny]


def _rule(
    *,
    record_above: bool = True,
    threshold: str = "1.0 mW",
    resume: str | None = None,
    dwell_seconds: float = 0.0,
) -> RecordingRule:
    return RecordingRule(
        threshold=ureg(threshold),
        resume_threshold=ureg(resume if resume is not None else threshold),
        record_above=record_above,
        dwell_seconds=dwell_seconds,
    )


def _gate(**kwargs: object) -> RecordingGate:
    return RecordingGate(_rule(**kwargs), sensor_id="laser-1")  # pyright: ignore[reportArgumentType]


# --------------------------------------------------------------------------
# the threshold itself
# --------------------------------------------------------------------------


def test_a_reading_above_the_threshold_is_recorded() -> None:
    gate = _gate()

    assert gate.admits(ureg("5.0 mW"), now=0.0)


def test_a_reading_below_the_threshold_is_not() -> None:
    gate = _gate()

    assert not gate.admits(ureg("0.2 mW"), now=0.0)


def test_the_mirror_case_records_below_instead() -> None:
    """A gauge on a vented chamber is meaningless *above* a pressure."""
    gate = _gate(record_above=False, threshold="1e-3 mbar")

    assert gate.admits(ureg("1e-7 mbar"), now=0.0)
    assert not gate.admits(ureg("1.0 mbar"), now=0.0)


def test_the_comparison_converts_units() -> None:
    """A threshold in mW must gate a reading arriving in W."""
    gate = _gate(threshold="1.0 mW")

    assert gate.admits(ureg("0.005 W"), now=0.0)
    assert not gate.admits(ureg("0.0002 W"), now=0.0)


def test_a_gate_starts_out_recording() -> None:
    """Erring towards keeping data: stopping is the decision that costs."""
    gate = _gate(dwell_seconds=30.0)

    # Below the threshold from the very first reading, but the dwell has
    # not elapsed, so this is still recorded.
    assert gate.admits(ureg("0.0 mW"), now=0.0)


# --------------------------------------------------------------------------
# dwell — conservative about stopping
# --------------------------------------------------------------------------


def test_a_brief_dip_does_not_stop_recording() -> None:
    gate = _gate(dwell_seconds=30.0)

    assert gate.admits(ureg("5.0 mW"), now=0.0)
    assert gate.admits(ureg("0.1 mW"), now=10.0)
    assert gate.admits(ureg("0.1 mW"), now=20.0)
    assert gate.admits(ureg("5.0 mW"), now=25.0)
    # The dip resolved, so the clock restarts: 40s is well past the dwell
    # counted from t=10, and must not stop anything.
    assert gate.admits(ureg("0.1 mW"), now=40.0)


def test_a_sustained_drop_stops_recording_once_the_dwell_elapses() -> None:
    gate = _gate(dwell_seconds=30.0)

    assert gate.admits(ureg("0.1 mW"), now=100.0)
    assert gate.admits(ureg("0.1 mW"), now=129.9)
    assert not gate.admits(ureg("0.1 mW"), now=130.0)


def test_without_a_dwell_the_first_low_reading_stops_it() -> None:
    gate = _gate()

    assert not gate.admits(ureg("0.1 mW"), now=0.0)


# --------------------------------------------------------------------------
# resume — eager
# --------------------------------------------------------------------------


def test_resuming_is_immediate_even_with_a_dwell() -> None:
    """Waiting would swallow the turn-on transient, which is the interesting part."""
    gate = _gate(dwell_seconds=30.0)

    assert gate.admits(ureg("0.1 mW"), now=0.0)
    assert not gate.admits(ureg("0.1 mW"), now=60.0)
    assert gate.admits(ureg("5.0 mW"), now=61.0)


def test_hysteresis_keeps_a_hovering_value_from_flapping() -> None:
    gate = _gate(threshold="1.0 mW", resume="10.0 mW")

    assert not gate.admits(ureg("0.9 mW"), now=0.0)
    # Above the stop threshold but below the resume threshold: still off.
    assert not gate.admits(ureg("5.0 mW"), now=1.0)
    assert gate.admits(ureg("11.0 mW"), now=2.0)


def test_without_a_resume_threshold_there_is_no_deadband() -> None:
    gate = _gate(threshold="1.0 mW")

    assert not gate.admits(ureg("0.9 mW"), now=0.0)
    assert gate.admits(ureg("1.1 mW"), now=1.0)


def test_the_dwell_clock_restarts_after_a_resume() -> None:
    gate = _gate(dwell_seconds=30.0)

    assert gate.admits(ureg("0.1 mW"), now=0.0)
    assert not gate.admits(ureg("0.1 mW"), now=30.0)
    assert gate.admits(ureg("5.0 mW"), now=31.0)
    # A fresh drop must serve the full dwell again rather than inheriting
    # the clock that stopped it the first time.
    assert gate.admits(ureg("0.1 mW"), now=32.0)
    assert not gate.admits(ureg("0.1 mW"), now=62.0)


# --------------------------------------------------------------------------
# transitions are logged, readings are not
# --------------------------------------------------------------------------


def test_stopping_logs_the_value_that_triggered_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = _gate()

    with caplog.at_level(logging.INFO):
        _ = gate.admits(ureg("0.2 mW"), now=0.0)

    (record,) = caplog.records
    assert record.message == "recording stopped"
    assert _field(record, "sensor_id") == "laser-1"
    assert "0.2" in str(_field(record, "value"))


def test_resuming_logs_too(caplog: pytest.LogCaptureFixture) -> None:
    gate = _gate()

    with caplog.at_level(logging.INFO):
        _ = gate.admits(ureg("0.2 mW"), now=0.0)
        _ = gate.admits(ureg("5.0 mW"), now=1.0)

    assert [record.message for record in caplog.records] == [
        "recording stopped",
        "recording resumed",
    ]


def test_a_steady_state_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """At 100 Hz a per-reading line would be unreadable."""
    gate = _gate()

    with caplog.at_level(logging.INFO):
        for tick in range(100):
            _ = gate.admits(ureg("5.0 mW"), now=float(tick))

    assert caplog.records == []


def test_a_sustained_outage_logs_once_rather_than_per_reading(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = _gate()

    with caplog.at_level(logging.INFO):
        for tick in range(100):
            _ = gate.admits(ureg("0.1 mW"), now=float(tick))

    assert len(caplog.records) == 1


# --------------------------------------------------------------------------
# the clock
# --------------------------------------------------------------------------


def test_now_defaults_to_the_monotonic_clock() -> None:
    """Callers in the sensor loop do not pass a time."""
    gate = _gate(dwell_seconds=30.0)

    assert gate.admits(ureg("0.1 mW"))
