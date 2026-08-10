import logging
import threading
import time

import pytest

from labmon import writer as writer_module
from labmon.writer import PointWriter


def _field(record: logging.LogRecord, name: str) -> object:
    """Read a structured log field off a record."""
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
    retries = [
        record for record in caplog.records if record.message == "write attempt failed"
    ]
    assert len(retries) == 2
    assert [_field(record, "attempt") for record in retries] == [1, 2]
    assert all(_field(record, "points") == 1 for record in retries)


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
    (record,) = [
        record
        for record in caplog.records
        if record.message == "dropping batch at shutdown"
    ]
    assert record.message == "dropping batch at shutdown"
    assert _field(record, "points") == 1


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
