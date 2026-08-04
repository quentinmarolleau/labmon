import logging

import pytest

from labmon.calibration import ureg
from labmon.gate import RecordingGate, StopRecordingRule


def _field(record: logging.LogRecord, name: str) -> object:
    """Read a logfmt field off a record.

    `extra=` fields become attributes a type checker cannot know about,
    so this says plainly that the lookup is dynamic.
    """
    return getattr(record, name)  # pyright: ignore[reportAny]


def _rule(
    *,
    below: str | None = None,
    above: str | None = None,
    resume_above: str | None = None,
    resume_below: str | None = None,
    dwell_seconds: float = 0.0,
    raw_voltage: bool = False,
) -> StopRecordingRule:
    return StopRecordingRule(
        below=None if below is None else ureg(below),
        above=None if above is None else ureg(above),
        resume_above=None if resume_above is None else ureg(resume_above),
        resume_below=None if resume_below is None else ureg(resume_below),
        dwell_seconds=dwell_seconds,
        raw_voltage=raw_voltage,
    )


def _gate(**kwargs: object) -> RecordingGate:
    """A gate on a photodiode, stopping below 1 mW unless told otherwise."""
    _ = kwargs.setdefault("below", "1.0 mW")
    return RecordingGate(_rule(**kwargs), sensor_id="laser-1")  # pyright: ignore[reportArgumentType]


def _admits(
    gate: RecordingGate, value: str, now: float, voltage: str = "0.0 V"
) -> bool:
    return gate.admits(ureg(value), ureg(voltage), now=now)


# --------------------------------------------------------------------------
# the bounds themselves
# --------------------------------------------------------------------------


def test_a_reading_inside_the_band_is_recorded() -> None:
    gate = _gate()

    assert _admits(gate, "5.0 mW", now=0.0)


def test_a_reading_below_the_lower_bound_is_not() -> None:
    gate = _gate()

    assert not _admits(gate, "0.2 mW", now=0.0)


def test_the_mirror_case_stops_above_instead() -> None:
    """A gauge on a vented chamber is meaningless *above* a pressure."""
    gate = RecordingGate(_rule(above="1e-3 mbar"), sensor_id="chamber-1")

    assert _admits(gate, "1e-7 mbar", now=0.0)
    assert not _admits(gate, "1.0 mbar", now=0.0)


def test_both_bounds_make_a_band_recording_has_to_stay_inside() -> None:
    gate = RecordingGate(_rule(below="100 mV", above="3.0 V"), sensor_id="quadrant-1")

    assert _admits(gate, "1.5 V", now=0.0)
    assert not _admits(gate, "50 mV", now=1.0)


def test_a_two_sided_band_stops_at_its_upper_bound_too() -> None:
    """A railed amplifier is as much an outage as a dark photodiode."""
    gate = RecordingGate(_rule(below="100 mV", above="3.0 V"), sensor_id="quadrant-1")

    assert not _admits(gate, "3.2 V", now=0.0)


def test_the_comparison_converts_units() -> None:
    """A bound in mW must gate a reading arriving in W."""
    gate = _gate(below="1.0 mW")

    assert _admits(gate, "0.005 W", now=0.0)
    assert not _admits(gate, "0.0002 W", now=0.0)


def test_a_gate_starts_out_recording() -> None:
    """Erring towards keeping data: stopping is the decision that costs."""
    gate = _gate(dwell_seconds=30.0)

    # Outside the band from the very first reading, but the dwell has not
    # elapsed, so this is still recorded.
    assert _admits(gate, "0.0 mW", now=0.0)


# --------------------------------------------------------------------------
# raw_voltage — gating before the conversion
# --------------------------------------------------------------------------


def test_raw_voltage_gates_on_the_voltage_not_the_value() -> None:
    gate = RecordingGate(_rule(above="3.0 V", raw_voltage=True), sensor_id="quadrant-1")

    # The converted value is far outside a millivolt band; only the
    # voltage is consulted, and 1.5 V is comfortably inside.
    assert _admits(gate, "500 mW", now=0.0, voltage="1.5 V")
    assert not _admits(gate, "500 mW", now=1.0, voltage="3.2 V")


def test_without_raw_voltage_the_voltage_is_ignored() -> None:
    gate = _gate(below="1.0 mW")

    # A voltage that would breach a volt-denominated bound, alongside a
    # value that is fine: the value is what this rule gates on.
    assert _admits(gate, "5.0 mW", now=0.0, voltage="0.0 V")


def test_raw_voltage_reports_the_voltage_in_the_transition(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = RecordingGate(_rule(above="3.0 V", raw_voltage=True), sensor_id="quadrant-1")

    with caplog.at_level(logging.INFO):
        _ = _admits(gate, "500 mW", now=0.0, voltage="3.2 V")

    (record,) = caplog.records
    assert "3.2 V" in str(_field(record, "value"))


# --------------------------------------------------------------------------
# dwell — conservative about stopping
# --------------------------------------------------------------------------


def test_a_brief_dip_does_not_stop_recording() -> None:
    gate = _gate(dwell_seconds=30.0)

    assert _admits(gate, "5.0 mW", now=0.0)
    assert _admits(gate, "0.1 mW", now=10.0)
    assert _admits(gate, "0.1 mW", now=20.0)
    assert _admits(gate, "5.0 mW", now=25.0)
    # The dip resolved, so the clock restarts: 40s is well past the dwell
    # counted from t=10, and must not stop anything.
    assert _admits(gate, "0.1 mW", now=40.0)


def test_a_sustained_drop_stops_recording_once_the_dwell_elapses() -> None:
    gate = _gate(dwell_seconds=30.0)

    assert _admits(gate, "0.1 mW", now=100.0)
    assert _admits(gate, "0.1 mW", now=129.9)
    assert not _admits(gate, "0.1 mW", now=130.0)


def test_the_dwell_spans_an_excursion_that_changes_side() -> None:
    """Outside the band is outside the band, whichever bound it left by."""
    gate = RecordingGate(
        _rule(below="100 mV", above="3.0 V", dwell_seconds=30.0),
        sensor_id="quadrant-1",
    )

    assert _admits(gate, "3.2 V", now=0.0)
    assert _admits(gate, "50 mV", now=20.0)
    # Never back inside the band, so the dwell runs from t=0 rather than
    # restarting when the reading crossed to the other side.
    assert not _admits(gate, "50 mV", now=30.0)


def test_without_a_dwell_the_first_reading_outside_stops_it() -> None:
    gate = _gate()

    assert not _admits(gate, "0.1 mW", now=0.0)


# --------------------------------------------------------------------------
# resume — eager
# --------------------------------------------------------------------------


def test_resuming_is_immediate_even_with_a_dwell() -> None:
    """Waiting would swallow the turn-on transient, which is the interesting part."""
    gate = _gate(dwell_seconds=30.0)

    assert _admits(gate, "0.1 mW", now=0.0)
    assert not _admits(gate, "0.1 mW", now=60.0)
    assert _admits(gate, "5.0 mW", now=61.0)


def test_a_deadband_keeps_a_hovering_value_from_flapping() -> None:
    gate = _gate(below="1.0 mW", resume_above="10.0 mW")

    assert not _admits(gate, "0.9 mW", now=0.0)
    # Back above the stop bound but below the resume bound: still off.
    assert not _admits(gate, "5.0 mW", now=1.0)
    assert _admits(gate, "11.0 mW", now=2.0)


def test_without_a_deadband_the_stop_bound_is_reused() -> None:
    gate = _gate(below="1.0 mW")

    assert not _admits(gate, "0.9 mW", now=0.0)
    assert _admits(gate, "1.1 mW", now=1.0)


def test_a_two_sided_resume_needs_the_reading_inside_both_edges() -> None:
    gate = RecordingGate(
        _rule(below="100 mV", above="3.0 V", resume_below="2.9 V"),
        sensor_id="quadrant-1",
    )

    assert not _admits(gate, "3.2 V", now=0.0)
    # Under the stop bound but not yet under the resume bound.
    assert not _admits(gate, "2.95 V", now=1.0)
    assert _admits(gate, "2.5 V", now=2.0)
    # And the other edge still applies once recording again.
    assert not _admits(gate, "50 mV", now=3.0)


def test_a_value_still_past_the_far_bound_does_not_resume() -> None:
    """resume_above < resume_below < value is outside the resume band, not inside it."""
    gate = RecordingGate(
        _rule(below="100 mV", above="3.0 V", resume_below="2.9 V"),
        sensor_id="quadrant-1",
    )

    assert not _admits(gate, "3.2 V", now=0.0)
    # Still above the resume ceiling, whether or not it is back under the
    # stop bound. Clearing the floor alone must not be enough.
    assert not _admits(gate, "3.5 V", now=1.0)
    assert not _admits(gate, "2.95 V", now=2.0)
    assert _admits(gate, "2.85 V", now=3.0)


def test_the_mirror_case_does_not_resume_under_the_resume_floor() -> None:
    gate = _gate(below="1.0 mW", resume_above="10.0 mW")

    assert not _admits(gate, "0.9 mW", now=0.0)
    assert not _admits(gate, "0.1 mW", now=1.0)
    assert not _admits(gate, "5.0 mW", now=2.0)
    assert _admits(gate, "11.0 mW", now=3.0)


# --------------------------------------------------------------------------
# the bounds themselves are outside the band, in both directions
# --------------------------------------------------------------------------


def test_a_reading_exactly_on_a_stop_bound_keeps_recording() -> None:
    """3 V is not above 3 V, and 1 mW is not below 1 mW."""
    gate = RecordingGate(_rule(below="1.0 mW", above="3.0 mW"), sensor_id="quadrant-1")

    assert _admits(gate, "3.0 mW", now=0.0)
    assert _admits(gate, "1.0 mW", now=1.0)


def test_a_reading_exactly_on_a_resume_bound_holds_the_gate_stopped() -> None:
    """Neither direction owns the boundary, so the gate keeps its state."""
    gate = _gate(below="1.0 mW", resume_above="10.0 mW")

    assert not _admits(gate, "0.5 mW", now=0.0)
    assert not _admits(gate, "10.0 mW", now=1.0)
    assert _admits(gate, "10.001 mW", now=2.0)


def test_the_boundary_convention_is_the_same_at_the_upper_edge() -> None:
    """The mirror of the case above, which used to disagree with it."""
    gate = RecordingGate(
        _rule(below="100 mV", above="3.0 V", resume_below="2.9 V"),
        sensor_id="quadrant-1",
    )

    assert not _admits(gate, "3.2 V", now=0.0)
    assert not _admits(gate, "2.9 V", now=1.0)
    assert _admits(gate, "2.899 V", now=2.0)


def test_the_dwell_clock_restarts_after_a_resume() -> None:
    gate = _gate(dwell_seconds=30.0)

    assert _admits(gate, "0.1 mW", now=0.0)
    assert not _admits(gate, "0.1 mW", now=30.0)
    assert _admits(gate, "5.0 mW", now=31.0)
    # A fresh drop must serve the full dwell again rather than inheriting
    # the clock that stopped it the first time.
    assert _admits(gate, "0.1 mW", now=32.0)
    assert not _admits(gate, "0.1 mW", now=62.0)


# --------------------------------------------------------------------------
# transitions are logged, readings are not
# --------------------------------------------------------------------------


def test_stopping_logs_the_value_and_the_bound_it_crossed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = _gate()

    with caplog.at_level(logging.INFO):
        _ = _admits(gate, "0.2 mW", now=0.0)

    (record,) = caplog.records
    assert record.message == "recording stopped"
    assert _field(record, "sensor_id") == "laser-1"
    assert "0.2" in str(_field(record, "value"))
    assert _field(record, "limit") == "below 1.0 mW"


def test_a_two_sided_gate_names_which_bound_stopped_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """'below 100 mV' and 'above 3 V' are very different faults to read about."""
    gate = RecordingGate(_rule(below="100 mV", above="3.0 V"), sensor_id="quadrant-1")

    with caplog.at_level(logging.INFO):
        _ = _admits(gate, "3.2 V", now=0.0)

    (record,) = caplog.records
    assert _field(record, "limit") == "above 3.0 V"


def test_resuming_logs_the_band_it_came_back_into(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = _gate(below="1.0 mW", resume_above="10.0 mW")

    with caplog.at_level(logging.INFO):
        _ = _admits(gate, "0.2 mW", now=0.0)
        _ = _admits(gate, "11.0 mW", now=1.0)

    stopped, resumed = caplog.records
    assert stopped.message == "recording stopped"
    assert resumed.message == "recording resumed"
    assert _field(resumed, "limit") == "above 10.0 mW"


def test_a_two_sided_resume_band_names_both_edges(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = RecordingGate(
        _rule(below="100 mV", above="3.0 V", resume_below="2.9 V"),
        sensor_id="quadrant-1",
    )

    with caplog.at_level(logging.INFO):
        _ = _admits(gate, "3.2 V", now=0.0)
        _ = _admits(gate, "2.5 V", now=1.0)

    _, resumed = caplog.records
    assert _field(resumed, "limit") == "above 100 mV and below 2.9 V"


def test_a_steady_state_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """At 100 Hz a per-reading line would be unreadable."""
    gate = _gate()

    with caplog.at_level(logging.INFO):
        for tick in range(100):
            _ = _admits(gate, "5.0 mW", now=float(tick))

    assert caplog.records == []


def test_a_sustained_outage_logs_once_rather_than_per_reading(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = _gate()

    with caplog.at_level(logging.INFO):
        for tick in range(100):
            _ = _admits(gate, "0.1 mW", now=float(tick))

    assert len(caplog.records) == 1


# --------------------------------------------------------------------------
# the clock
# --------------------------------------------------------------------------


def test_now_defaults_to_the_monotonic_clock() -> None:
    """Callers in the sensor loop do not pass a time."""
    gate = _gate(dwell_seconds=30.0)

    assert gate.admits(ureg("0.1 mW"), ureg("0.0 V"))
