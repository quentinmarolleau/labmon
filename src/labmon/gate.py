"""Stop recording a signal while the instrument producing it is off.

Some signals are only meaningful while their instrument is running. A
photodiode watching a laser reads noise overnight; a gauge on a vented
chamber reads atmosphere; a channel whose amplifier has railed reads the
supply. Recorded at full rate those readings cost storage, stretch
dashboard axes, and drag every average and alert threshold computed over
the series.

A rule here is a band the readings must stay inside. It is written as the
conditions that *stop* recording — below a level, above a level, or both
— because that is the decision being made: the interesting instrument
state is the one being excluded. Everything between the bounds is
recorded; anything outside them stops the channel until it comes back.

The gap that leaves is the honest signal, and the transition is logged so
the gap can be read: with the `logs` profile a flat trace says "laser off
at 19:42" rather than "sensor died at some point last night".

The two directions are deliberately asymmetric, because their costs are.
Stopping wrongly loses real data, so it can be made conservative with a
dwell. Resuming wrongly costs a handful of junk samples, so it is always
immediate — waiting would swallow the turn-on transient, which is often
the most interesting part of the trace.
"""

import logging
import time
from dataclasses import dataclass

import pint

logger: logging.Logger = logging.getLogger(__name__)

# Digits kept when a transition reports the value that caused it. Enough
# to recognise the reading, few enough to stay readable in a log line.
_VALUE_DIGITS = 4


def _above(value: pint.Quantity, limit: pint.Quantity) -> bool:
    """Whether `value` is strictly above `limit`.

    Converted to the limit's unit and compared as magnitudes, so a
    reading arriving in W is gated correctly by a bound written in mW.
    """
    return float(value.to(limit.units).magnitude) > float(limit.magnitude)


def _describe_band(floor: pint.Quantity | None, ceiling: pint.Quantity | None) -> str:
    """Phrase a band for a log line, naming only the bounds it has."""
    edges: list[str] = []
    if floor is not None:
        edges.append(f"above {floor:~}")
    if ceiling is not None:
        edges.append(f"below {ceiling:~}")
    return " and ".join(edges)


@dataclass(frozen=True)
class StopRecordingRule:
    """The band a channel's readings must stay inside to be recorded.

    `below` and `above` are the levels that stop recording; either may be
    absent, giving a one-sided rule. A reading is recorded while it sits
    between them.

    `resume_above` and `resume_below` bound the narrower band a reading
    must come back into before recording restarts, each defaulting to the
    stop level it deadbands. Written apart from the stop levels because
    they answer a different question: how far back inside the band the
    signal has to get before the instrument counts as on again.

    `raw_voltage` gates on the voltage at the ADC input rather than on
    the converted value, for a channel whose off state is better
    recognised before the conversion — a railed amplifier, or a
    conversion that is only characterised over part of the input range.

    Every bound is dimensioned, so a comparison against a reading in
    another unit converts rather than silently comparing magnitudes.
    """

    below: pint.Quantity | None = None
    above: pint.Quantity | None = None
    resume_above: pint.Quantity | None = None
    resume_below: pint.Quantity | None = None
    dwell_seconds: float = 0.0
    raw_voltage: bool = False

    @property
    def resume_floor(self) -> pint.Quantity | None:
        """Lower edge of the resume band; `below` when there is no deadband."""
        return self.below if self.resume_above is None else self.resume_above

    @property
    def resume_ceiling(self) -> pint.Quantity | None:
        """Upper edge of the resume band; `above` when there is no deadband."""
        return self.above if self.resume_below is None else self.resume_below

    @property
    def resume_band(self) -> str:
        """The resume band, phrased for the line that logs a resume."""
        return _describe_band(self.resume_floor, self.resume_ceiling)

    def breach(self, value: pint.Quantity) -> str | None:
        """How a reading falls outside the recording band, or None if it doesn't.

        Returns the phrase rather than a bare bool so the bound that was
        crossed reaches the log without being worked out twice — on a
        two-sided rule, "below 100 mV" and "above 3 V" are very different
        faults to read about at 03:00.
        """
        if self.below is not None and not _above(value, self.below):
            return _describe_band(None, self.below)
        if self.above is not None and _above(value, self.above):
            return _describe_band(self.above, None)
        return None

    def resumes(self, value: pint.Quantity) -> bool:
        """Whether a reading is back inside the resume band."""
        floor, ceiling = self.resume_floor, self.resume_ceiling
        clears_floor = floor is None or _above(value, floor)
        clears_ceiling = ceiling is None or not _above(value, ceiling)
        return clears_floor and clears_ceiling


class RecordingGate:
    """Applies a `StopRecordingRule` over time, with its dwell and deadband.

    Stateful by nature — a deadband is a memory of what happened last —
    so the rule stays a frozen value and only the two things that
    genuinely change over time live here.

    Starts out recording. A gate that has seen nothing yet has no reason
    to believe the instrument is off, and erring towards keeping data is
    the whole asymmetry of this module.
    """

    def __init__(self, rule: StopRecordingRule, sensor_id: str) -> None:
        self._rule: StopRecordingRule = rule
        self._sensor_id: str = sensor_id
        self._recording: bool = True
        # When the value first strayed outside the band, or None while it
        # is inside. The dwell is measured from here.
        self._failing_since: float | None = None

    def admits(
        self, value: pint.Quantity, voltage: pint.Quantity, now: float | None = None
    ) -> bool:
        """Whether this reading should be written, updating the gate's state.

        Both quantities are passed because the rule decides which one it
        gates on; picking here would put `raw_voltage` in the acquisition
        loop, where it does not belong.

        `now` is injectable for tests; the sensor loop passes nothing and
        gets the monotonic clock, which is immune to the wall clock
        stepping mid-run.
        """
        if now is None:
            now = time.monotonic()

        measured = voltage if self._rule.raw_voltage else value
        if self._recording:
            return self._while_recording(measured, now)
        # No `now`: resuming never consults the clock, which is the
        # asymmetry this module exists for.
        return self._while_stopped(measured)

    def _while_recording(self, measured: pint.Quantity, now: float) -> bool:
        breach = self._rule.breach(measured)
        if breach is None:
            # An excursion that resolved: the clock restarts rather than
            # accumulating across separate ones.
            self._failing_since = None
            return True

        if self._failing_since is None:
            self._failing_since = now
        if now - self._failing_since < self._rule.dwell_seconds:
            # Still inside the dwell, so keep recording. A brief
            # excursion costs nothing; stopping on it would lose real data.
            return True

        self._recording = False
        self._log("recording stopped", measured, breach)
        return False

    def _while_stopped(self, measured: pint.Quantity) -> bool:
        if not self._rule.resumes(measured):
            return False

        self._recording = True
        # A later excursion serves the full dwell again rather than
        # inheriting the clock that stopped it the first time.
        self._failing_since = None
        self._log("recording resumed", measured, self._rule.resume_band)
        return True

    def _log(self, message: str, measured: pint.Quantity, limit: str) -> None:
        """Report a transition, never a reading.

        At 100 Hz a per-reading line would be unreadable, and the
        transition is the event worth seeing anyway.
        """
        logger.info(
            message,
            extra={
                "sensor_id": self._sensor_id,
                "value": f"{measured.magnitude:.{_VALUE_DIGITS}g} {measured.units:~}",
                "limit": limit,
            },
        )
