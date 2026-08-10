"""Background, queue-backed writer so producers never block on InfluxDB I/O."""

import logging
import queue
import threading
from typing import Protocol

logger: logging.Logger = logging.getLogger(__name__)

# Defaults for the constructor arguments below, named so they can be
# quoted in docs/configuration.md and referred to from a call site that
# wants to change one of them without restating the others.

# How many points may queue up before write() starts blocking. At ten
# thousand a 100 Hz sensor rides out a ~100s outage before it does.
DEFAULT_QUEUE_MAXSIZE = 10_000

# How long the background thread waits on an empty queue before
# re-checking for a stop request. Bounds how long close() can take.
DEFAULT_POLL_INTERVAL_SECONDS = 0.5

# The first retry wait after a failed batch, and the ceiling the
# doubling stops at.
DEFAULT_INITIAL_BACKOFF_SECONDS = 1.0
DEFAULT_MAX_BACKOFF_SECONDS = 30.0


class _Writable[T](Protocol):
    def write(self, batch: list[T], /) -> None: ...
    def close(self) -> None: ...


class PointWriter[T]:
    """Buffers points in a queue and writes them from a background thread.

    write() only enqueues and returns immediately; a dedicated thread drains
    the queue and calls the client's write() with as many points as have
    accumulated since the last flush, amortizing the cost of each call
    across a batch instead of paying it once per point.

    poll_interval bounds how long close() can take to notice a stop
    request when the queue is empty (the background thread re-checks for
    a stop roughly every poll_interval seconds while idle).

    A batch that fails to write is retried with exponential backoff from
    initial_backoff up to max_backoff (interruptible by close()) rather
    than being dropped or left to kill the background thread — a real
    network to a remote server can have transient outages that a
    same-host connection never would. A batch still failing when close()
    is called is logged and dropped rather than retried indefinitely, so
    shutdown stays prompt.

    All four are arguments rather than module constants because the right
    values depend on the deployment: a 100 Hz board on a flaky link wants
    a deeper queue than a thermometer sampled every minute. Passing one
    survives an upgrade; editing installed source does not.
    """

    def __init__(
        self,
        client: _Writable[T],
        maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        initial_backoff: float = DEFAULT_INITIAL_BACKOFF_SECONDS,
        max_backoff: float = DEFAULT_MAX_BACKOFF_SECONDS,
    ) -> None:
        self._client: _Writable[T] = client
        self._queue: queue.Queue[T] = queue.Queue(maxsize=maxsize)
        self._poll_interval: float = poll_interval
        self._initial_backoff: float = initial_backoff
        self._max_backoff: float = max_backoff
        self._stop: threading.Event = threading.Event()
        self._thread: threading.Thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def write(self, point: T) -> None:
        """Queue a point to be written; never blocks on network I/O."""
        self._queue.put(point)

    def close(self) -> None:
        """Flush any remaining queued points, then stop the writer thread."""
        self._stop.set()
        self._thread.join()
        self._client.close()

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                batch: list[T] = [self._queue.get(timeout=self._poll_interval)]
            except queue.Empty:
                continue
            while True:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            self._write_with_retry(batch)

    def _write_with_retry(self, batch: list[T]) -> None:
        """Write a batch, retrying with backoff on failure until it succeeds.

        If close() is requested while a batch is failing, the batch is
        logged and dropped instead of retried forever, so close() stays
        responsive even during a sustained outage.
        """
        backoff = self._initial_backoff
        attempt = 1
        while True:
            try:
                self._client.write(batch)
                return
            except Exception:
                logger.warning(
                    "write attempt failed",
                    extra={
                        "attempt": attempt,
                        "points": len(batch),
                        "retry_in_s": f"{backoff:.0f}",
                    },
                    exc_info=True,
                )
            if self._stop.wait(timeout=backoff):
                logger.error("dropping batch at shutdown", extra={"points": len(batch)})
                return
            backoff = min(backoff * 2, self._max_backoff)
            attempt += 1
