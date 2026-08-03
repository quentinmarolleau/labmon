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
import signal
import sys
import time
from collections import Counter
from types import FrameType
from typing import Protocol

from influxdb_client_3 import Point

from labmon.influx import get_client
from labmon.writer import PointWriter

logger: logging.Logger = logging.getLogger(__name__)

# How often to report that readings are still arriving. One line per
# reading is unreadable at any real rate, but silence gives an operator no
# way to tell "working" from "wedged".
SUMMARY_INTERVAL_SECONDS = 30.0


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
    """

    def __init__(
        self,
        *,
        closes: Closeable | None = None,
        summary_interval: float | None = SUMMARY_INTERVAL_SECONDS,
    ) -> None:
        self.writer: PointWriter[Point] = PointWriter[Point](get_client())
        self._summary_interval: float | None = summary_interval
        self._written: Counter[str] = Counter()
        self._next_summary: float = time.monotonic() + (summary_interval or 0.0)

        def shutdown(_signum: int, _frame: FrameType | None) -> None:
            if closes is not None:
                closes.close()
            self.writer.close()
            sys.exit(0)

        _ = signal.signal(signal.SIGINT, shutdown)
        _ = signal.signal(signal.SIGTERM, shutdown)

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
        logger.info(
            "Wrote %d reading(s) in the last %.0fs (%s)",
            self._written.total(),
            self._summary_interval,
            ", ".join(
                f"{name} {count}" for name, count in sorted(self._written.items())
            )
            or "none",
        )
        self._written.clear()
        self._next_summary = now + self._summary_interval
