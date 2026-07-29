import logging
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from types import FrameType

import pytest
from influxdb_client_3 import Point

from labmon.calibration import Calibration, LinearConversion, ureg
from labmon.sensors import serial_sensor
from labmon.sensors.serial_sensor import main, run
from labmon.sensors.serial_source import RawReading

SignalHandler = Callable[[int, FrameType | None], None]


class _StopLoop(Exception):
    pass


class FakeInfluxClient:
    def __init__(self) -> None:
        self.batches: list[list[Point]] = []
        self.closed: bool = False

    def write(self, batch: list[Point]) -> None:
        self.batches.append(list(batch))

    def close(self) -> None:
        self.closed = True


class FakeRawSource:
    """Replays canned readings, then stops the run loop."""

    def __init__(self, readings: list[RawReading | None]) -> None:
        self._readings: list[RawReading | None] = list(readings)
        self.closed: bool = False

    def read(self) -> RawReading | None:
        if not self._readings:
            raise _StopLoop
        return self._readings.pop(0)

    def close(self) -> None:
        self.closed = True


def _temperature_calibration(store_input: bool = True) -> Calibration:
    return Calibration(
        sensor_id="cryo-77k",
        measurement="temperature",
        conversion=LinearConversion(factor=ureg("42.5 kelvin / volt")),
        store_input=store_input,
    )


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeInfluxClient:
    client = FakeInfluxClient()
    monkeypatch.setattr(serial_sensor, "get_client", lambda: client)
    return client


@pytest.fixture
def registered_handlers(monkeypatch: pytest.MonkeyPatch) -> dict[int, SignalHandler]:
    handlers: dict[int, SignalHandler] = {}

    def fake_signal(signalnum: int, handler: SignalHandler) -> None:
        handlers[signalnum] = handler

    monkeypatch.setattr(signal, "signal", fake_signal)
    return handlers


def test_run_writes_a_calibrated_point(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    source = FakeRawSource([RawReading(channel="A0", raw_count=4095)])

    with pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": _temperature_calibration()})

    shutdown = registered_handlers[signal.SIGINT]
    with pytest.raises(SystemExit) as exit_info:
        shutdown(signal.SIGINT, None)
    assert exit_info.value.code == 0

    [batch] = fake_client.batches
    [point] = batch
    line = point.to_line_protocol()
    # A full-scale count is ~3.3V, so 3.3 * 42.5 K/V is ~140.25 K, and
    # the unit tag comes from pint's own short-form symbol. Line protocol
    # orders fields alphabetically, so the input precedes the value.
    assert line.startswith("temperature,sensor_id=cryo-77k,unit=K ")
    assert "value=140.2" in line
    assert "input_volts=3.3" in line


def test_run_stores_the_conversion_input_by_default(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    # Half of full scale is ~1.65V; keeping it makes a wrong calibration
    # correctable after the fact.
    source = FakeRawSource([RawReading(channel="A0", raw_count=2048)])

    with pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": _temperature_calibration()})

    # The writer batches on a background thread; closing it flushes.
    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    [batch] = fake_client.batches
    [point] = batch
    assert "input_volts=1.65" in point.to_line_protocol()


def test_run_omits_the_conversion_input_when_the_channel_opts_out(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    source = FakeRawSource([RawReading(channel="A0", raw_count=4095)])

    with pytest.raises(_StopLoop):
        run(
            source=source,
            calibrations={"A0": _temperature_calibration(store_input=False)},
        )

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    [batch] = fake_client.batches
    [point] = batch
    line = point.to_line_protocol()
    assert "input_volts" not in line
    assert "value=140.2" in line


@pytest.mark.usefixtures("fake_client")
def test_run_registers_both_shutdown_signals(
    registered_handlers: dict[int, SignalHandler],
) -> None:
    with pytest.raises(_StopLoop):
        run(source=FakeRawSource([]), calibrations={})

    assert signal.SIGINT in registered_handlers
    assert signal.SIGTERM in registered_handlers


def test_run_closes_the_source_and_writer_on_shutdown(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    source = FakeRawSource([])

    with pytest.raises(_StopLoop):
        run(source=source, calibrations={})

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGTERM, None)

    assert source.closed is True
    assert fake_client.closed is True


def test_run_skips_reads_that_produced_nothing(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    # None is what a read timeout looks like; it must not write a point.
    source = FakeRawSource([None, None])

    with pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": _temperature_calibration()})

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    assert fake_client.batches == []


def test_run_warns_once_per_uncalibrated_channel(
    fake_client: FakeInfluxClient,
    registered_handlers: dict[int, SignalHandler],
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = FakeRawSource(
        [
            RawReading(channel="A7", raw_count=1),
            RawReading(channel="A7", raw_count=2),
            RawReading(channel="A0", raw_count=4095),
        ]
    )

    with caplog.at_level(logging.WARNING), pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": _temperature_calibration()})

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    # A board streaming an unmapped channel shouldn't flood the log.
    assert caplog.text.count("No calibration for channel") == 1
    assert "A7" in caplog.text
    [batch] = fake_client.batches
    assert len(batch) == 1


def test_run_honours_a_custom_adc_resolution_and_reference(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    source = FakeRawSource([RawReading(channel="A0", raw_count=1023)])

    with pytest.raises(_StopLoop):
        run(
            source=source,
            calibrations={"A0": _temperature_calibration()},
            resolution_bits=10,
            v_ref=5.0,
        )

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    [batch] = fake_client.batches
    [point] = batch
    # Full scale on a 10-bit/5V ADC is 5V, so 5 * 42.5 K/V is 212.5 K.
    assert "value=212.5" in point.to_line_protocol()


def _patch_main_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    run_calls: list[dict[str, object]] = []
    port_calls: list[dict[str, object]] = []

    def fake_run(**kwargs: object) -> None:
        run_calls.append(kwargs)

    def fake_open_serial_port(port: str, baudrate: int) -> object:
        port_calls.append({"port": port, "baudrate": baudrate})
        return object()

    def fake_load_calibration(_path: Path) -> dict[str, Calibration]:
        return {"A0": _temperature_calibration()}

    monkeypatch.setattr(serial_sensor, "run", fake_run)
    monkeypatch.setattr(serial_sensor, "open_serial_port", fake_open_serial_port)
    monkeypatch.setattr(serial_sensor, "load_calibration", fake_load_calibration)
    return run_calls, port_calls


def test_main_parses_defaults_and_calls_run(monkeypatch: pytest.MonkeyPatch) -> None:
    run_calls, port_calls = _patch_main_dependencies(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        ["serial-sensor", "--port", "/dev/labmon-due", "--calibration", "cal.toml"],
    )

    main()

    assert port_calls == [{"port": "/dev/labmon-due", "baudrate": 115200}]
    [call] = run_calls
    assert call["resolution_bits"] == 12
    assert call["v_ref"] == 3.3
    assert call["calibrations"] == {"A0": _temperature_calibration()}


def test_main_parses_custom_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    run_calls, port_calls = _patch_main_dependencies(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "serial-sensor",
            "--port",
            "/dev/ttyACM0",
            "--calibration",
            "/etc/labmon/cal.toml",
            "--baudrate",
            "9600",
            "--resolution-bits",
            "10",
            "--vref",
            "5.0",
        ],
    )

    main()

    assert port_calls == [{"port": "/dev/ttyACM0", "baudrate": 9600}]
    [call] = run_calls
    assert call["resolution_bits"] == 10
    assert call["v_ref"] == 5.0
