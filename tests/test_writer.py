import logging
import queue
import threading
import time

import pytest

from labmon import writer as writer_module
from labmon.writer import PointWriter


def _field(record: logging.LogRecord, name: str) -> object:
    """Read a logfmt field off a record.

    `extra=` fields become attributes a type checker cannot know about,
    so this says plainly that the lookup is dynamic.
    """
    return getattr(record, name)  # pyright: ignore[reportAny]


class FakeClient[T]:
    def __init__(self) -> None:
        self.batches: list[list[T]] = []
        self.closed: threading.Event = threading.Event()

    def write(self, batch: list[T]) -> None:
        self.batches.append(list(batch))

    def close(self) -> None:
        self.closed.set()


class FlakyClient[T]:
    """Fails write() a fixed number of times, then succeeds."""

    def __init__(self, fail_times: int) -> None:
        self._fail_times: int = fail_times
        self.attempts: int = 0
        self.batches: list[list[T]] = []
        self.closed: threading.Event = threading.Event()

    def write(self, batch: list[T]) -> None:
        self.attempts += 1
        if self.attempts <= self._fail_times:
            raise ConnectionError("simulated transient network failure")
        self.batches.append(list(batch))

    def close(self) -> None:
        self.closed.set()


class AlwaysFailingClient[T]:
    """Fails every write() call, simulating a sustained outage."""

    def __init__(self) -> None:
        self.attempts: int = 0
        self.closed: threading.Event = threading.Event()

    def write(self, _batch: list[T]) -> None:
        self.attempts += 1
        raise ConnectionError("simulated persistent outage")

    def close(self) -> None:
        self.closed.set()


class StalledClient[T]:
    """Blocks inside write() until released, so the queue can be filled.

    Parking the drain thread *inside* the client makes the queue's
    contents deterministic: nothing is being taken off it while the test
    puts points on.
    """

    def __init__(self) -> None:
        self.entered: threading.Event = threading.Event()
        self.release: threading.Event = threading.Event()
        self.batches: list[list[T]] = []
        self.closed: threading.Event = threading.Event()

    def write(self, batch: list[T]) -> None:
        self.batches.append(list(batch))
        self.entered.set()
        _ = self.release.wait(timeout=5)

    def close(self) -> None:
        self.closed.set()


def _stalled_writer(maxsize: int) -> tuple[PointWriter[int], StalledClient[int]]:
    """A writer whose drain thread is parked inside the client."""
    client = StalledClient[int]()
    writer = PointWriter[int](client, maxsize=maxsize)
    writer.write(0)
    assert client.entered.wait(timeout=5), "drain thread never reached the client"
    return writer, client


def test_a_full_queue_drops_the_oldest_and_keeps_acquiring() -> None:
    """The point of the policy: an outage must not stop acquisition.

    Blocking here would convert a storage outage into an acquisition
    outage, and silently — the summary that would report it is emitted
    by the loop that would have stopped.
    """
    writer, client = _stalled_writer(maxsize=3)

    for point in (1, 2, 3, 4, 5):
        writer.write(point)

    assert writer.dropped == 2

    client.release.set()
    writer.close()

    written = [point for batch in client.batches for point in batch]
    assert written == [0, 3, 4, 5], "the newest points should survive, not the oldest"


def test_dropping_warns_once_rather_than_per_point(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A full queue is a per-point event; one line is enough to say so."""
    writer, client = _stalled_writer(maxsize=2)

    with caplog.at_level(logging.WARNING):
        for point in range(1, 8):
            writer.write(point)

    client.release.set()
    writer.close()

    dropped = [
        r
        for r in caplog.records
        if r.getMessage() == "queue full; dropping oldest readings"
    ]
    assert len(dropped) == 1
    assert dropped[0].__dict__["maxsize"] == 2


def test_dropped_is_zero_while_the_queue_has_room() -> None:
    client = FakeClient[int]()
    writer = PointWriter[int](client, maxsize=100)

    for point in range(10):
        writer.write(point)
    writer.close()

    assert writer.dropped == 0


def test_a_queue_drained_mid_discard_costs_no_point() -> None:
    """The narrow race between a failed put and the discard that follows.

    `put_nowait` can fail on a full queue, and the drain thread can then
    empty it before `_discard_oldest` reaches for a point. Nothing should
    be counted as dropped: the space it was trying to make appeared on
    its own, which is the outcome that was wanted.

    Modelled by failing the *first* get only, which is what the real race
    looks like — a genuinely empty queue accepts the retried put, so the
    loop terminates rather than spinning.
    """
    client = StalledClient[int]()
    writer = PointWriter[int](client, maxsize=1)
    writer.write(0)
    assert client.entered.wait(timeout=5)

    queue_ = writer._queue  # pyright: ignore[reportPrivateUsage]
    real_get = queue_.get_nowait
    calls = 0

    def _empty_once() -> int:
        """Stand in for the drain thread winning the race.

        It takes the point *and* reports the queue as empty, which is
        exactly what `_discard_oldest` observes when the drain thread
        gets there first.
        """
        nonlocal calls
        calls += 1
        if calls == 1:
            _ = real_get()
            raise queue.Empty
        return real_get()

    queue_.put_nowait(1)
    queue_.get_nowait = _empty_once
    writer.write(2)
    queue_.get_nowait = real_get

    assert calls == 1, "the discard should have reached for a point exactly once"
    assert writer.dropped == 0, "no point was actually discarded"

    client.release.set()
    writer.close()


def test_write_does_not_block_the_caller() -> None:
    client = FakeClient[int]()
    writer = PointWriter[int](client)

    start = time.monotonic()
    for i in range(50):
        writer.write(i)
    elapsed = time.monotonic() - start

    writer.close()
    assert elapsed < 0.1


def test_close_flushes_all_queued_points_and_closes_client() -> None:
    client = FakeClient[int]()
    writer = PointWriter[int](client)

    for i in range(10):
        writer.write(i)
    writer.close()

    written = [point for batch in client.batches for point in batch]
    assert written == list(range(10))
    assert client.closed.is_set()


def test_survives_idle_polling_before_a_point_arrives() -> None:
    client = FakeClient[int]()
    writer = PointWriter[int](client, poll_interval=0.01)

    time.sleep(0.05)
    writer.write(1)
    writer.close()

    written = [point for batch in client.batches for point in batch]
    assert written == [1]


def test_write_retries_transient_failures_then_succeeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FlakyClient[int](fail_times=2)
    writer = PointWriter[int](
        client, poll_interval=0.01, initial_backoff=0.01, max_backoff=0.01
    )

    with caplog.at_level(logging.WARNING):
        writer.write(1)
        time.sleep(0.1)
        writer.close()

    written = [point for batch in client.batches for point in batch]
    assert written == [1]
    assert client.attempts == 3
    retries = [r for r in caplog.records if r.getMessage() == "write attempt failed"]
    assert [_field(r, "attempt") for r in retries] == [1, 2]
    assert all(_field(r, "points") == 1 for r in retries)


def test_close_drops_a_batch_still_failing_at_shutdown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = AlwaysFailingClient[int]()
    writer = PointWriter[int](client, poll_interval=0.01)

    writer.write(1)
    time.sleep(0.05)

    start = time.monotonic()
    with caplog.at_level(logging.ERROR):
        writer.close()
    elapsed = time.monotonic() - start

    assert elapsed < 1.0
    assert client.attempts >= 1
    assert client.closed.is_set()
    # Asserting on the field rather than the sentence: the batch size is
    # the part that matters, and a reworded message should not fail here.
    [dropped] = [
        r
        for r in caplog.records
        if r.getMessage() == "dropping an unwritten batch at shutdown"
    ]
    assert _field(dropped, "points") == 1


# --------------------------------------------------------------------------
# Every knob is a parameter, not an edit to installed source
# --------------------------------------------------------------------------


def test_the_backoff_defaults_are_the_documented_ones() -> None:
    """docs/configuration.md quotes these, so a change must break a test."""
    assert writer_module.DEFAULT_QUEUE_MAXSIZE == 10_000
    assert writer_module.DEFAULT_POLL_INTERVAL_SECONDS == 0.5
    assert writer_module.DEFAULT_INITIAL_BACKOFF_SECONDS == 1.0
    assert writer_module.DEFAULT_MAX_BACKOFF_SECONDS == 30.0


def test_the_first_retry_waits_the_backoff_it_was_given(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A one-second default would not fit three attempts in this window."""
    client = FlakyClient[int](fail_times=2)
    writer = PointWriter[int](
        client, poll_interval=0.01, initial_backoff=0.01, max_backoff=0.01
    )

    with caplog.at_level(logging.CRITICAL, logger="labmon.writer"):
        writer.write(1)
        time.sleep(0.1)
        writer.close()

    assert client.attempts == 3


def test_the_cap_stops_the_backoff_doubling_away(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Uncapped doubling from 1ms reaches ~64ms by the eighth attempt.

    Capping at 2ms keeps every wait short, so a fixed window fits far
    more attempts than doubling would — which is what the cap is for.
    """
    client = AlwaysFailingClient[int]()
    writer = PointWriter[int](
        client, poll_interval=0.001, initial_backoff=0.001, max_backoff=0.002
    )

    with caplog.at_level(logging.CRITICAL, logger="labmon.writer"):
        writer.write(1)
        time.sleep(0.2)
        writer.close()

    assert client.attempts > 20
