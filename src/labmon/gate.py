"""Stop recording a signal while the instrument producing it is off.

Some signals are only meaningful while their instrument is running. A
photodiode watching a laser reads noise overnight; a gauge on a vented
chamber reads atmosphere. Recorded at full rate those readings cost
storage, stretch dashboard axes, and drag every average and alert
threshold computed over the series.

A gate is a per-channel threshold that suppresses them. The gap it leaves
is the honest signal, and the transition is logged so the gap can be read:
with the `logs` profile a flat trace says "laser off at 19:42" rather than
"sensor died at some point last night".

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


@dataclass(frozen=True)
class RecordingRule:
    """When a channel's readings are worth recording.

    `record_above` picks the direction: True records while the value is
    above `threshold` (a photodiode), False while it is below (a pressure
    gauge). `resume_threshold` equal to `threshold` means no hysteresis.

    Both thresholds are dimensioned, so a comparison against a reading in
    another unit converts rather than silently comparing magnitudes.
    """

    threshold: pint.Quantity
    resume_threshold: pint.Quantity
    record_above: bool
    dwell_seconds: float = 0.0

    def _passes(self, value: pint.Quantity, limit: pint.Quantity) -> bool:
        # Converted to the limit's unit and compared as magnitudes, so a
        # reading arriving in W is gated correctly by a threshold in mW.
        measured = float(value.to(limit.units).magnitude)
        boundary = float(limit.magnitude)
        return measured > boundary if self.record_above else measured < boundary

    def keeps_recording(self, value: pint.Quantity) -> bool:
        """Whether a reading is on the recording side of the stop threshold."""
        return self._passes(value, self.threshold)

    def starts_recording(self, value: pint.Quantity) -> bool:
        """Whether a reading is far enough past the threshold to resume."""
        return self._passes(value, self.resume_threshold)


class RecordingGate:
    """Applies a `RecordingRule` over time, with its dwell and hysteresis.

    Stateful by nature — hysteresis is a memory of what happened last —
    so the rule stays a frozen value and only the two things that
    genuinely change over time live here.

    Starts out recording. A gate that has seen nothing yet has no reason
    to believe the instrument is off, and erring towards keeping data is
    the whole asymmetry of this module.
    """

    def __init__(self, rule: RecordingRule, sensor_id: str) -> None:
        self._rule: RecordingRule = rule
        self._sensor_id: str = sensor_id
        self._recording: bool = True
        # When the value first fell to the wrong side, or None while it
        # is on the right side. The dwell is measured from here.
        self._failing_since: float | None = None

    def admits(self, value: pint.Quantity, now: float | None = None) -> bool:
        """Whether this reading should be written, updating the gate's state.

        `now` is injectable for tests; the sensor loop passes nothing and
        gets the monotonic clock, which is immune to the wall clock
        stepping mid-run.
        """
        if now is None:
            now = time.monotonic()

        if self._recording:
            return self._while_recording(value, now)
        # No `now`: resuming never consults the clock, which is the
        # asymmetry this module exists for.
        return self._while_stopped(value)

    def _while_recording(self, value: pint.Quantity, now: float) -> bool:
        if self._rule.keeps_recording(value):
            # A dip that resolved: the clock restarts rather than
            # accumulating across separate excursions.
            self._failing_since = None
            return True

        if self._failing_since is None:
            self._failing_since = now
        if now - self._failing_since < self._rule.dwell_seconds:
            # Still inside the dwell, so keep recording. A brief dip
            # costs nothing; stopping on it would lose real data.
            return True

        self._recording = False
        self._log("recording stopped", value, self._rule.threshold)
        return False

    def _while_stopped(self, value: pint.Quantity) -> bool:
        if not self._rule.starts_recording(value):
            return False

        self._recording = True
        # A later drop serves the full dwell again rather than inheriting
        # the clock that stopped it the first time.
        self._failing_since = None
        self._log("recording resumed", value, self._rule.resume_threshold)
        return True

    def _log(
        self, message: str, value: pint.Quantity, threshold: pint.Quantity
    ) -> None:
        """Report a transition, never a reading.

        At 100 Hz a per-reading line would be unreadable, and the
        transition is the event worth seeing anyway.
        """
        logger.info(
            message,
            extra={
                "sensor_id": self._sensor_id,
                "value": f"{value.magnitude:.{_VALUE_DIGITS}g} {value.units:~}",
                "threshold": f"{threshold.magnitude:.{_VALUE_DIGITS}g}"
                + f" {threshold.units:~}",
            },
        )
