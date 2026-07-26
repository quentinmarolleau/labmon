"""Background, queue-backed writer so producers never block on InfluxDB I/O."""

import queue
import threading


class PointWriter:
    """Buffers points in a queue and writes them from a background thread.

    write() only enqueues and returns immediately; a dedicated thread drains
    the queue and calls the client's write() with as many points as have
    accumulated since the last flush, amortizing the cost of each call
    across a batch instead of paying it once per point.
    """

    def __init__(self, client, maxsize: int = 10_000):
        self._client = client
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def write(self, point) -> None:
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
                batch = [self._queue.get(timeout=0.5)]
            except queue.Empty:
                continue
            while True:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            self._client.write(batch)
