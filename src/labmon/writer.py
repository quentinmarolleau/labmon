"""Background, queue-backed writer so producers never block on InfluxDB I/O."""

import logging
import queue
import threading
from typing import Protocol

logger: logging.Logger = logging.getLogger(__name__)

_INITIAL_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 30.0


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

    A batch that fails to write is retried with exponential backoff
    (capped, and interruptible by close()) rather than being dropped or
    left to kill the background thread — a real network to a remote
    server can have transient outages that a same-host connection never
    would. A batch still failing when close() is called is logged and
    dropped rather than retried indefinitely, so shutdown stays prompt.
    """

    def __init__(
        self, client: _Writable[T], maxsize: int = 10_000, poll_interval: float = 0.5
    ) -> None:
        self._client: _Writable[T] = client
        self._queue: queue.Queue[T] = queue.Queue(maxsize=maxsize)
        self._poll_interval: float = poll_interval
        self._stop: threading.Event = threading.Event()
        self._thread: threading.Thread = threading.Thread(
            target=self._run, daemon=True
        )
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
        backoff = _INITIAL_BACKOFF_SECONDS
        attempt = 1
        while True:
            try:
                self._client.write(batch)
                return
            except Exception:
                logger.warning(
                    "Write attempt %d failed for a batch of %d point(s);"
                    + " retrying in %.0fs",
                    attempt,
                    len(batch),
                    backoff,
                    exc_info=True,
                )
            if self._stop.wait(timeout=backoff):
                logger.error(
                    "Dropping a batch of %d point(s) still unwritten at shutdown",
                    len(batch),
                )
                return
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
            attempt += 1
