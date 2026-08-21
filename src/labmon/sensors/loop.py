"""The plumbing every sensor loop needs, so none of them writes it again.

Three sensors reach InfluxDB by different routes — a simulated walk, a
board on a serial line, a vendor API — and all three were separately
building a writer, installing the same two signal handlers, counting what
they had written, and emitting the same periodic summary. The summary in
particular existed twice, verbatim.

What is shared is the plumbing, not the loop. How a reading is obtained
and how the loop is paced are genuinely different: one sleeps a fixed
interval, one blocks on a serial read, one backs off when a vendor API
throws. Those stay with each sensor. What moves here is everything that
was the same.
"""

import logging
import math
import signal
import sys
import time
from collections import Counter
from collections.abc import Iterable
from types import FrameType
from typing import Protocol

from influxdb_client_3 import Point

from labmon.influx import get_client
from labmon.writer import PointWriter

logger: logging.Logger = logging.getLogger(__name__)

# How often to report that readings are still arriving. One line per
# reading is unreadable at any real rate, but silence gives an operator no
# way to tell "working" from "wedged". A default rather than a fixed
# constant: both sensor entry points expose it as --summary-interval.
DEFAULT_SUMMARY_INTERVAL_SECONDS = 30.0


class Closeable(Protocol):
    """Anything a sensor holds open that shutdown must release."""

    def close(self) -> None: ...


class SensorLoop:
    """Owns the writer, the shutdown handlers and the periodic summary.

    Constructing one installs SIGINT and SIGTERM handlers, so it belongs
    in the main thread — which is where a sensor's `run()` already is.

    `closes` is anything else shutdown must release, such as an open
    serial port. It is closed before the writer, so nothing new arrives
    while the queue is draining.

    `summary_interval` of None disables the summary, for a sensor that
    already reports every reading.

    `writer` is how a deployment tunes the queue: build a `PointWriter`
    with the depth and backoff that suit the link, and hand it over.
    Taking the writer rather than forwarding each of its arguments keeps
    this signature from growing every time the writer gains one — and a
    caller who supplies a writer already holds the client, so none is
    opened here.
    """

    def __init__(
        self,
        *,
        closes: Closeable | None = None,
        summary_interval: float | None = DEFAULT_SUMMARY_INTERVAL_SECONDS,
        writer: PointWriter[Point] | None = None,
        sensors: Iterable[str] = (),
    ) -> None:
        self.writer: PointWriter[Point] = writer or PointWriter[Point](get_client())
        self._summary_interval: float | None = summary_interval
        # The sensors this loop is responsible for, whether or not they
        # ever produce anything. Without them the summary can only report
        # sensors that were seen, so a board that says nothing says nothing
        # here either — and silence is the state the summary exists to
        # distinguish from a dead process.
        self._sensors: frozenset[str] = frozenset(sensors)
        self._written: Counter[str] = Counter()
        self._skipped: Counter[str] = Counter()
        self._warned: set[str] = set()
        self._next_summary: float = time.monotonic() + (summary_interval or 0.0)

        def shutdown(_signum: int, _frame: FrameType | None) -> None:
            if closes is not None:
                closes.close()
            self.writer.close()
            sys.exit(0)

        _ = signal.signal(signal.SIGINT, shutdown)
        _ = signal.signal(signal.SIGTERM, shutdown)

    def admits(self, value: float, *, sensor_id: str) -> bool:
        """Whether a reading can be written, warning once if it cannot.

        A conversion can produce `nan` or `inf` without raising — `log(v -
        2.0)` below two volts, an affine factor large enough to overflow, a
        spline through awkward points. `float()` accepts both, so the value
        reaches InfluxDB looking like any other, and one of them poisons
        every average, minimum and maximum over that series from then on.
        `serial_source.parse_reading` already guards the input side for the
        same reason; this is the output side.

        Refusing is not enough on its own, because a gap in a trace looks
        the same as a dead sensor. So the first refusal for a sensor says
        so at WARNING, and every refusal is counted into the periodic
        summary. One line establishes that it is happening; the count
        establishes how much, which is what separates a transient from a
        calibration that is wrong across half its range.

        Warning once per sensor rather than once per reading is the shape
        `serial_sensor` already uses for a channel with no calibration.
        """
        if math.isfinite(value):
            return True
        self._skipped[sensor_id] += 1
        if sensor_id not in self._warned:
            self._warned.add(sensor_id)
            logger.warning(
                "conversion produced a non-finite value; skipping those readings",
                extra={"sensor_id": sensor_id, "value": repr(value)},
            )
        return False

    def record(self, point: Point, sensor_id: str) -> None:
        """Queue a point and count it towards the next summary."""
        self.writer.write(point)
        self._written[sensor_id] += 1

    def summarise_if_due(self) -> None:
        """Report what has been written, if the interval has elapsed.

        Called from the sensor's own loop rather than from a timer, so
        the count is only ever read from the thread that writes it.
        """
        if self._summary_interval is None:
            return
        now = time.monotonic()
        if now < self._next_summary:
            return
        # One line per sensor rather than one line listing all of them:
        # each carries its own sensor_id field, so a collector can label
        # it and a query for one instrument returns only its own lines.
        # Sensors that only skipped are reported too, which is the case
        # most worth seeing: the trace is flat, and this line is the only
        # thing that can say the readings arrived and were unwritable.
        for sensor_id in sorted(
            self._sensors | set(self._written) | set(self._skipped)
        ):
            logger.info(
                "wrote readings",
                extra={
                    "sensor_id": sensor_id,
                    "readings": self._written[sensor_id],
                    "skipped": self._skipped[sensor_id],
                    "window_s": f"{self._summary_interval:.0f}",
                },
            )
        self._written.clear()
        self._skipped.clear()
        self._next_summary = now + self._summary_interval
