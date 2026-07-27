import logging
import threading
import time

import pytest

from labmon import writer as writer_module
from labmon.writer import PointWriter


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
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(writer_module, "_INITIAL_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr(writer_module, "_MAX_BACKOFF_SECONDS", 0.01)
    client = FlakyClient[int](fail_times=2)
    writer = PointWriter[int](client, poll_interval=0.01)

    with caplog.at_level(logging.WARNING):
        writer.write(1)
        time.sleep(0.1)
        writer.close()

    written = [point for batch in client.batches for point in batch]
    assert written == [1]
    assert client.attempts == 3
    assert caplog.text.count("Write attempt") == 2


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
    assert "Dropping a batch of 1 point(s)" in caplog.text
