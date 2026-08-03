import logging
import signal
import time
from collections.abc import Callable
from types import FrameType

import pytest
from influxdb_client_3 import Point

from labmon.sensors import loop as sensor_loop
from labmon.sensors.loop import SensorLoop

SignalHandler = Callable[[int, FrameType | None], None]


def _field(record: logging.LogRecord, name: str) -> object:
    """Read a logfmt field off a record.

    `extra=` fields become attributes a type checker cannot know about,
    so this says plainly that the lookup is dynamic.
    """
    return getattr(record, name)  # pyright: ignore[reportAny]


class FakeInfluxClient:
    def __init__(self) -> None:
        self.batches: list[list[Point]] = []
        self.closed: bool = False

    def write(self, batch: list[Point]) -> None:
        self.batches.append(list(batch))

    def close(self) -> None:
        self.closed = True

    @property
    def points(self) -> list[Point]:
        return [point for batch in self.batches for point in batch]


class FakePort:
    """Something a sensor holds open, recording when it was released."""

    def __init__(self) -> None:
        self.closed: bool = False
        self.closed_before_writer: bool | None = None

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeInfluxClient:
    client = FakeInfluxClient()
    monkeypatch.setattr(sensor_loop, "get_client", lambda: client)
    return client


@pytest.fixture
def registered_handlers(monkeypatch: pytest.MonkeyPatch) -> dict[int, SignalHandler]:
    handlers: dict[int, SignalHandler] = {}

    def fake_signal(signalnum: int, handler: SignalHandler) -> None:
        handlers[signalnum] = handler

    monkeypatch.setattr(signal, "signal", fake_signal)
    return handlers


def _point(value: float) -> Point:
    return Point("m").tag("sensor_id", "s").field("value", value)


@pytest.mark.usefixtures("registered_handlers")
def test_record_queues_the_point(fake_client: FakeInfluxClient) -> None:
    loop = SensorLoop()

    loop.record(_point(1.0), "s")
    loop.writer.close()

    assert len(fake_client.points) == 1


@pytest.mark.usefixtures("fake_client")
def test_both_shutdown_signals_are_registered(
    registered_handlers: dict[int, SignalHandler],
) -> None:
    _ = SensorLoop()

    assert signal.SIGINT in registered_handlers
    assert signal.SIGTERM in registered_handlers


def test_shutdown_closes_the_port_before_the_writer(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    """Order matters: nothing new should arrive while the queue drains."""
    port = FakePort()
    original_close = FakeInfluxClient.close

    def record_order(client: FakeInfluxClient) -> None:
        port.closed_before_writer = port.closed
        original_close(client)

    FakeInfluxClient.close = record_order  # pyright: ignore[reportAttributeAccessIssue]
    try:
        _ = SensorLoop(closes=port)
        with pytest.raises(SystemExit) as exit_info:
            registered_handlers[signal.SIGTERM](signal.SIGTERM, None)
    finally:
        FakeInfluxClient.close = original_close

    assert exit_info.value.code == 0
    assert port.closed is True
    assert port.closed_before_writer is True
    assert fake_client.closed is True


@pytest.mark.usefixtures("fake_client", "registered_handlers")
def test_the_summary_names_each_sensor_and_its_count(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ticks = iter([0.0, sensor_loop.SUMMARY_INTERVAL_SECONDS + 1.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    loop = SensorLoop()
    loop.record(_point(1.0), "cryo-77k")
    loop.record(_point(2.0), "cryo-77k")
    loop.record(_point(3.0), "room-1")

    with caplog.at_level(logging.INFO, logger=sensor_loop.logger.name):
        loop.summarise_if_due()

    # One record per sensor, each naming its own — so a collector can
    # label them individually rather than parsing one combined sentence.
    summaries = [r for r in caplog.records if r.getMessage() == "wrote readings"]
    assert [(_field(r, "sensor_id"), _field(r, "readings")) for r in summaries] == [
        ("cryo-77k", 2),
        ("room-1", 1),
    ]
    assert all(_field(r, "window_s") == "30" for r in summaries)


@pytest.mark.usefixtures("fake_client", "registered_handlers")
def test_the_summary_waits_for_its_interval(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ticks = iter([0.0, 1.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    loop = SensorLoop()
    loop.record(_point(1.0), "s")

    with caplog.at_level(logging.INFO, logger=sensor_loop.logger.name):
        loop.summarise_if_due()

    assert "wrote readings" not in caplog.text


@pytest.mark.usefixtures("fake_client", "registered_handlers")
def test_a_loop_without_a_summary_never_emits_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """For a sensor that already reports every reading, like mock-sensor."""
    loop = SensorLoop(summary_interval=None)
    loop.record(_point(1.0), "s")

    with caplog.at_level(logging.INFO, logger=sensor_loop.logger.name):
        loop.summarise_if_due()

    assert "wrote readings" not in caplog.text
