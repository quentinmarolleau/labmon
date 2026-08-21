import logging
import signal
import time
from collections.abc import Callable
from types import FrameType
from typing import override

import pytest
from influxdb_client_3 import Point

from labmon.sensors import loop as sensor_loop
from labmon.sensors import polling
from labmon.sensors.polling import build_point, poll, write_reading

SignalHandler = Callable[[int, FrameType | None], None]


def _field(record: logging.LogRecord, name: str) -> object:
    """Read a logfmt field off a record.

    `extra=` fields become attributes a type checker cannot know about,
    so this says plainly that the lookup is dynamic.
    """
    return getattr(record, name)  # pyright: ignore[reportAny]


class _StopLoop(BaseException):
    """Breaks out of poll()'s infinite loop.

    Deliberately a BaseException, unlike the equivalent in
    test_serial_sensor: poll() catches Exception around every read so a
    misbehaving vendor API cannot end the process, which would swallow an
    ordinary exception used for control flow here and hang the suite.
    """


class FakeInfluxClient:
    """Records batches, and whether it was closed before the test looked."""

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


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeInfluxClient:
    client = FakeInfluxClient()
    # Two seams: poll() gets its client through SensorLoop, while
    # write_reading() opens one directly because it must flush before it
    # returns. Both are patched to the same fake.
    monkeypatch.setattr(sensor_loop, "get_client", lambda: client)
    monkeypatch.setattr(polling, "get_client", lambda: client)
    return client


@pytest.fixture
def registered_handlers(monkeypatch: pytest.MonkeyPatch) -> dict[int, SignalHandler]:
    handlers: dict[int, SignalHandler] = {}

    def fake_signal(signalnum: int, handler: SignalHandler) -> None:
        handlers[signalnum] = handler

    monkeypatch.setattr(signal, "signal", fake_signal)
    return handlers


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Runs the loop at full speed, recording what it would have waited."""
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(time, "sleep", fake_sleep)
    return slept


# --------------------------------------------------------------------------
# build_point
# --------------------------------------------------------------------------


def test_build_point_carries_the_identifying_tags() -> None:
    line = build_point(
        21.5, sensor_id="cryo-1", measurement="temperature", unit="degC"
    ).to_line_protocol()

    assert line.startswith("temperature,")
    assert "sensor_id=cryo-1" in line
    assert "unit=degC" in line
    assert "value=21.5" in line


def test_build_point_omits_an_empty_unit() -> None:
    # A dimensionless reading should not carry an empty tag, which would
    # create a second series indistinguishable from the first.
    line = build_point(1.0, sensor_id="c", measurement="m").to_line_protocol()

    assert "unit=" not in line


def test_build_point_accepts_extra_tags_and_a_field_name() -> None:
    line = build_point(
        7.0,
        sensor_id="c",
        measurement="m",
        field="setpoint",
        tags={"rack": "A3"},
    ).to_line_protocol()

    assert "rack=A3" in line
    assert "setpoint=7" in line


# --------------------------------------------------------------------------
# write_reading — the one-shot path
# --------------------------------------------------------------------------


def test_write_reading_writes_one_point(fake_client: FakeInfluxClient) -> None:
    write_reading(4.2, sensor_id="cryo-4k", measurement="temperature", unit="K")

    [point] = fake_client.points
    assert "sensor_id=cryo-4k" in point.to_line_protocol()


def test_write_reading_has_flushed_before_it_returns(
    fake_client: FakeInfluxClient,
) -> None:
    """The whole reason this is not PointWriter.

    A one-shot process exits the moment this returns; a point still sitting
    in a background thread's queue would die with it.
    """
    write_reading(1.0, sensor_id="c", measurement="m")

    assert fake_client.points != []
    assert fake_client.closed is True


def test_write_reading_closes_the_client_even_when_the_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Failing(FakeInfluxClient):
        @override
        def write(self, batch: list[Point]) -> None:
            raise RuntimeError("influx unreachable")

    client = Failing()
    monkeypatch.setattr(polling, "get_client", lambda: client)

    with pytest.raises(RuntimeError, match="influx unreachable"):
        write_reading(1.0, sensor_id="c", measurement="m")

    # Otherwise a timer-driven script leaks a connection on every failure.
    assert client.closed is True


# --------------------------------------------------------------------------
# poll — the continuous path
# --------------------------------------------------------------------------


def _stop_after(values: list[float | None]) -> Callable[[], float | None]:
    remaining = list(values)

    def read() -> float | None:
        if not remaining:
            raise _StopLoop
        return remaining.pop(0)

    return read


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_build_point_refuses_a_non_finite_value(value: float) -> None:
    """The primitive will not construct a point that poisons a series."""
    with pytest.raises(ValueError, match="non-finite"):
        _ = build_point(value, sensor_id="c", measurement="m")


def test_write_reading_skips_a_non_finite_value_without_raising(
    fake_client: FakeInfluxClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A one-shot run under a systemd timer must not fail on a bad read.

    Restart=on-failure would turn one NaN from a vendor API into a
    restart loop, so this reports and exits cleanly having written
    nothing.
    """
    with caplog.at_level(logging.WARNING, logger=polling.logger.name):
        write_reading(float("nan"), sensor_id="c", measurement="m")

    assert fake_client.points == []
    # No connection is opened at all: there is nothing to send, and a
    # timer-driven script should not touch the network to decide that.
    assert fake_client.closed is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert _field(warnings[0], "sensor_id") == "c"


@pytest.mark.usefixtures("no_sleep")
def test_poll_keeps_going_after_a_non_finite_reading(
    fake_client: FakeInfluxClient,
    registered_handlers: dict[int, SignalHandler],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The transient case: good, two bad, good again.

    The valid readings either side must still be written — a conversion
    that fails at one voltage says nothing about the rest of the range.
    """
    with (
        caplog.at_level(logging.WARNING, logger=sensor_loop.logger.name),
        pytest.raises(_StopLoop),
    ):
        poll(
            _stop_after([1.0, float("nan"), float("nan"), 2.0]),
            sensor_id="c",
            measurement="m",
        )

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGTERM](signal.SIGTERM, None)

    values = [
        point.to_line_protocol().split("value=")[1].split()[0]
        for point in fake_client.points
    ]
    assert values == ["1", "2"]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


@pytest.mark.usefixtures("no_sleep")
def test_poll_flushes_everything_it_read_on_shutdown(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    with pytest.raises(_StopLoop):
        poll(_stop_after([1.0, 2.0, 3.0]), sensor_id="c", measurement="m")

    with pytest.raises(SystemExit) as exit_info:
        registered_handlers[signal.SIGTERM](signal.SIGTERM, None)

    assert exit_info.value.code == 0
    # Line protocol is "<measurement>,<tags> value=<v> <timestamp>".
    values = [
        point.to_line_protocol().split("value=")[1].split()[0]
        for point in fake_client.points
    ]
    assert values == ["1", "2", "3"]
    assert fake_client.closed is True


@pytest.mark.usefixtures("fake_client", "registered_handlers", "no_sleep")
def test_poll_registers_both_shutdown_signals(
    registered_handlers: dict[int, SignalHandler],
) -> None:
    with pytest.raises(_StopLoop):
        poll(_stop_after([]), sensor_id="c", measurement="m")

    assert signal.SIGINT in registered_handlers
    assert signal.SIGTERM in registered_handlers


@pytest.mark.usefixtures("registered_handlers", "no_sleep")
def test_poll_skips_a_tick_when_read_returns_none(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    # None means "no reading this time" — a device warming up, not an error.
    with pytest.raises(_StopLoop):
        poll(_stop_after([None, 2.0, None]), sensor_id="c", measurement="m")

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    assert len(fake_client.points) == 1


@pytest.mark.usefixtures("registered_handlers", "fake_client")
def test_poll_waits_the_interval_between_readings(no_sleep: list[float]) -> None:
    with pytest.raises(_StopLoop):
        poll(_stop_after([1.0, 2.0]), sensor_id="c", measurement="m", interval=2.5)

    assert no_sleep == [2.5, 2.5]


# --------------------------------------------------------------------------
# poll — a failing vendor API must not end the process
# --------------------------------------------------------------------------


@pytest.mark.usefixtures("registered_handlers", "no_sleep")
def test_poll_survives_a_read_that_raises(
    fake_client: FakeInfluxClient,
    registered_handlers: dict[int, SignalHandler],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A vendor SDK throwing at 03:00 must not stop monitoring."""
    calls = iter([RuntimeError("device busy"), RuntimeError("device busy"), 42.0])

    def read() -> float | None:
        try:
            outcome = next(calls)
        except StopIteration:
            raise _StopLoop from None
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with (
        caplog.at_level(logging.WARNING, logger=polling.logger.name),
        pytest.raises(_StopLoop),
    ):
        poll(read, sensor_id="c", measurement="m", interval=1.0)

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    assert len(fake_client.points) == 1
    assert "device busy" in caplog.text


@pytest.mark.usefixtures("registered_handlers", "fake_client")
def test_poll_backs_off_while_reads_keep_failing(no_sleep: list[float]) -> None:
    """Backoff, so a dead device is not hammered once per interval."""
    failures = iter(range(4))

    def read() -> float | None:
        try:
            _ = next(failures)
        except StopIteration:
            raise _StopLoop from None
        raise RuntimeError("unreachable")

    with pytest.raises(_StopLoop):
        poll(read, sensor_id="c", measurement="m", interval=1.0)

    # Each failure waits longer than the last, rather than retrying at the
    # nominal interval forever.
    assert no_sleep == sorted(no_sleep)
    assert no_sleep[-1] > no_sleep[0]


@pytest.mark.usefixtures("registered_handlers", "fake_client")
def test_poll_backoff_is_capped(no_sleep: list[float]) -> None:
    failures = iter(range(30))

    def read() -> float | None:
        try:
            _ = next(failures)
        except StopIteration:
            raise _StopLoop from None
        raise RuntimeError("unreachable")

    with pytest.raises(_StopLoop):
        poll(read, sensor_id="c", measurement="m", interval=1.0)

    assert max(no_sleep) == polling.DEFAULT_MAX_BACKOFF_SECONDS


@pytest.mark.usefixtures("registered_handlers", "fake_client")
def test_poll_backoff_bounds_are_parameters(no_sleep: list[float]) -> None:
    """A device known to need a minute to reboot should not be guessed at."""
    failures = iter(range(30))

    def read() -> float | None:
        try:
            _ = next(failures)
        except StopIteration:
            raise _StopLoop from None
        raise RuntimeError("unreachable")

    with pytest.raises(_StopLoop):
        poll(
            read,
            sensor_id="c",
            measurement="m",
            interval=1.0,
            initial_backoff=5.0,
            max_backoff=20.0,
        )

    assert no_sleep[0] == 5.0
    assert max(no_sleep) == 20.0


def test_the_backoff_defaults_are_the_documented_ones() -> None:
    """docs/configuration.md quotes these, so a change must break a test."""
    assert polling.DEFAULT_INITIAL_BACKOFF_SECONDS == 1.0
    assert polling.DEFAULT_MAX_BACKOFF_SECONDS == 60.0


@pytest.mark.usefixtures("registered_handlers", "fake_client")
def test_poll_returns_to_the_normal_interval_after_a_recovery(
    no_sleep: list[float],
) -> None:
    remaining: list[float | RuntimeError] = [
        RuntimeError("x"),
        RuntimeError("x"),
        1.0,
        2.0,
    ]

    def read() -> float | None:
        if not remaining:
            raise _StopLoop
        outcome = remaining.pop(0)
        if isinstance(outcome, RuntimeError):
            raise outcome
        return outcome

    with pytest.raises(_StopLoop):
        poll(read, sensor_id="c", measurement="m", interval=1.0)

    # The last two waits are the nominal interval again, not the backoff.
    assert no_sleep[-2:] == [1.0, 1.0]


@pytest.mark.usefixtures("fake_client", "registered_handlers", "no_sleep")
def test_poll_summarises_once_the_interval_elapses(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The per-reading line is DEBUG, so this carries "it is still working"."""
    # The clock passes the summary interval only on the second reading.
    ticks = iter([0.0, 1.0, sensor_loop.DEFAULT_SUMMARY_INTERVAL_SECONDS + 1.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))

    with (
        caplog.at_level(logging.INFO, logger=sensor_loop.logger.name),
        pytest.raises(_StopLoop),
    ):
        poll(_stop_after([1.0, 2.0]), sensor_id="c", measurement="m")

    summaries = [r for r in caplog.records if r.getMessage() == "wrote readings"]
    assert [(_field(r, "sensor_id"), _field(r, "readings")) for r in summaries] == [
        ("c", 2)
    ]


@pytest.mark.usefixtures("fake_client", "registered_handlers", "no_sleep")
def test_poll_passes_its_summary_interval_to_the_loop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A sensor that reports another way should be able to turn it off."""
    # Well past the default interval, so a dropped argument would summarise.
    ticks = iter([0.0, 1.0, sensor_loop.DEFAULT_SUMMARY_INTERVAL_SECONDS + 1.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))

    with (
        caplog.at_level(logging.INFO, logger=sensor_loop.logger.name),
        pytest.raises(_StopLoop),
    ):
        poll(
            _stop_after([1.0, 2.0]),
            sensor_id="c",
            measurement="m",
            summary_interval=None,
        )

    assert "wrote readings" not in caplog.text
