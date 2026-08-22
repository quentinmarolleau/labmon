import logging
import signal
import time
from collections.abc import Callable
from types import FrameType

import pytest
from influxdb_client_3 import Point

from labmon.sensors import loop as sensor_loop
from labmon.sensors.loop import SensorLoop
from labmon.writer import PointWriter

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
    ticks = iter([0.0, sensor_loop.DEFAULT_SUMMARY_INTERVAL_SECONDS + 1.0])
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
def test_a_declared_sensor_is_reported_even_having_done_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The silent-instrument case, which the counters alone cannot see.

    `_written` and `_skipped` only know about sensors that produced
    something. A board that says nothing at all leaves both empty, so a
    summary built from them is not merely zero — it is absent, and an
    absent line reads as a dead process rather than a quiet one.
    """
    ticks = iter([0.0, sensor_loop.DEFAULT_SUMMARY_INTERVAL_SECONDS + 1.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    loop = SensorLoop(sensors=("cryo-diode", "room-1"))

    with caplog.at_level(logging.INFO, logger=sensor_loop.logger.name):
        loop.summarise_if_due()

    summaries = [r for r in caplog.records if r.getMessage() == "wrote readings"]
    assert [
        (_field(r, "sensor_id"), _field(r, "readings"), _field(r, "skipped"))
        for r in summaries
    ] == [("cryo-diode", 0, 0), ("room-1", 0, 0)]


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


# --------------------------------------------------------------------------
# Readings a conversion made non-finite
# --------------------------------------------------------------------------


@pytest.mark.usefixtures("fake_client", "registered_handlers")
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_reading_is_refused_and_warned_about(
    value: float, caplog: pytest.LogCaptureFixture
) -> None:
    loop = SensorLoop()

    with caplog.at_level(logging.WARNING, logger=sensor_loop.logger.name):
        admitted = loop.admits(value, sensor_id="cryo-diode")

    assert admitted is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert _field(warnings[0], "sensor_id") == "cryo-diode"


@pytest.mark.usefixtures("fake_client", "registered_handlers")
def test_a_finite_reading_is_admitted_without_a_word(
    caplog: pytest.LogCaptureFixture,
) -> None:
    loop = SensorLoop()

    with caplog.at_level(logging.DEBUG, logger=sensor_loop.logger.name):
        admitted = loop.admits(1.0, sensor_id="cryo-diode")

    assert admitted is True
    assert caplog.records == []


@pytest.mark.usefixtures("fake_client", "registered_handlers")
def test_the_warning_is_once_per_sensor_not_once_per_reading(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A calibration wrong below some voltage fires on every reading.

    One line saying so is the signal; the rest is the summary's job.
    """
    loop = SensorLoop()

    with caplog.at_level(logging.WARNING, logger=sensor_loop.logger.name):
        for _ in range(3):
            _ = loop.admits(float("nan"), sensor_id="cryo-diode")
        _ = loop.admits(float("nan"), sensor_id="room-1")

    warned = [_field(r, "sensor_id") for r in caplog.records]
    assert warned == ["cryo-diode", "room-1"]


@pytest.mark.usefixtures("fake_client", "registered_handlers")
def test_the_summary_counts_what_was_skipped(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ticks = iter([0.0, sensor_loop.DEFAULT_SUMMARY_INTERVAL_SECONDS + 1.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    loop = SensorLoop()
    loop.record(_point(1.0), "cryo-diode")
    _ = loop.admits(float("nan"), sensor_id="cryo-diode")
    _ = loop.admits(float("nan"), sensor_id="cryo-diode")

    with caplog.at_level(logging.INFO, logger=sensor_loop.logger.name):
        loop.summarise_if_due()

    summaries = [r for r in caplog.records if r.getMessage() == "wrote readings"]
    assert [(_field(r, "readings"), _field(r, "skipped")) for r in summaries] == [
        (1, 2)
    ]


@pytest.mark.usefixtures("fake_client", "registered_handlers")
def test_the_summary_reports_points_dropped_by_a_full_queue(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Reported apart from `skipped`, because it is a different failure.

    `skipped` counts readings that arrived and could not be converted —
    a problem with the reading. A drop means the reading was fine and
    storage could not keep up, which points somewhere else entirely.

    It is also writer-wide rather than per-sensor: one PointWriter
    carries every channel, so the count cannot honestly be attributed to
    one of them.
    """
    ticks = iter([0.0, sensor_loop.DEFAULT_SUMMARY_INTERVAL_SECONDS + 1.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    loop = SensorLoop()
    loop.record(_point(1.0), "cryo-diode")

    # Stand in for a writer that has been shedding load.
    def _seven(_self: object) -> int:
        return 7

    monkeypatch.setattr(type(loop.writer), "dropped", property(_seven))

    with caplog.at_level(logging.INFO, logger=sensor_loop.logger.name):
        loop.summarise_if_due()

    [dropped] = [
        r
        for r in caplog.records
        if r.getMessage() == "dropped readings to keep acquiring"
    ]
    assert _field(dropped, "dropped") == 7
    assert dropped.levelno == logging.WARNING


@pytest.mark.usefixtures("fake_client", "registered_handlers")
def test_the_summary_is_silent_about_drops_when_there_are_none(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A line every window saying `dropped=0` is noise, not reassurance."""
    ticks = iter([0.0, sensor_loop.DEFAULT_SUMMARY_INTERVAL_SECONDS + 1.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    loop = SensorLoop()
    loop.record(_point(1.0), "cryo-diode")

    with caplog.at_level(logging.INFO, logger=sensor_loop.logger.name):
        loop.summarise_if_due()

    assert not [
        r
        for r in caplog.records
        if r.getMessage() == "dropped readings to keep acquiring"
    ]


@pytest.mark.usefixtures("fake_client", "registered_handlers")
def test_a_sensor_that_only_skipped_is_still_reported(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Otherwise a channel producing nothing but NaN vanishes from the log.

    Which is the case most worth seeing: the trace is flat and the
    summary is the only thing that can say why.
    """
    ticks = iter([0.0, sensor_loop.DEFAULT_SUMMARY_INTERVAL_SECONDS + 1.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    loop = SensorLoop()
    _ = loop.admits(float("nan"), sensor_id="cryo-diode")

    with caplog.at_level(logging.INFO, logger=sensor_loop.logger.name):
        loop.summarise_if_due()

    summaries = [r for r in caplog.records if r.getMessage() == "wrote readings"]
    assert [
        (_field(r, "sensor_id"), _field(r, "readings"), _field(r, "skipped"))
        for r in summaries
    ] == [("cryo-diode", 0, 1)]


@pytest.mark.usefixtures("fake_client", "registered_handlers")
def test_skips_do_not_carry_into_the_next_window(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    interval = sensor_loop.DEFAULT_SUMMARY_INTERVAL_SECONDS
    ticks = iter([0.0, interval + 1.0, 2 * interval + 2.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    loop = SensorLoop()
    _ = loop.admits(float("nan"), sensor_id="cryo-diode")

    with caplog.at_level(logging.INFO, logger=sensor_loop.logger.name):
        loop.summarise_if_due()
        caplog.clear()
        loop.summarise_if_due()

    assert "wrote readings" not in caplog.text


# --------------------------------------------------------------------------
# Tuning the writer without editing installed source
# --------------------------------------------------------------------------


@pytest.mark.usefixtures("registered_handlers")
def test_a_supplied_writer_is_used_instead_of_a_default_one() -> None:
    """How a deployment tunes the queue: build the writer, hand it over.

    Forwarding every PointWriter argument through SensorLoop would grow
    a parameter each time the writer gains one; taking the writer itself
    stays one parameter forever.
    """
    client = FakeInfluxClient()
    writer = PointWriter[Point](client, maxsize=5, poll_interval=0.01)
    loop = SensorLoop(writer=writer)

    loop.record(_point(1.0), "s")
    loop.writer.close()

    assert loop.writer is writer
    assert len(client.points) == 1


def test_supplying_a_writer_does_not_open_a_second_client(
    monkeypatch: pytest.MonkeyPatch, registered_handlers: dict[int, SignalHandler]
) -> None:
    """A caller who built the writer already holds the only connection."""

    def unexpected() -> FakeInfluxClient:  # pragma: no cover - must not run
        raise AssertionError("get_client() was called despite a supplied writer")

    monkeypatch.setattr(sensor_loop, "get_client", unexpected)
    client = FakeInfluxClient()
    loop = SensorLoop(writer=PointWriter[Point](client, poll_interval=0.01))

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGTERM](signal.SIGTERM, None)

    assert loop.writer is not None
    assert client.closed is True


def test_the_summary_default_is_the_documented_one() -> None:
    """docs/configuration.md quotes it, so a change must break a test."""
    assert sensor_loop.DEFAULT_SUMMARY_INTERVAL_SECONDS == 30.0
