import math
import signal
import sys
import time
from collections.abc import Callable
from types import FrameType

import pytest
from influxdb_client_3 import Point

from labmon.sensors import mock_sensor
from labmon.sensors.mock_sensor import RandomWalk, main, run

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


def test_next_reverts_toward_setpoint_on_average() -> None:
    walk = RandomWalk(setpoint=20.0, noise=0.0)
    walk.value = 25.0

    _ = walk.next()

    assert walk.value < 25.0


def test_next_holds_steady_at_setpoint_with_no_noise() -> None:
    walk = RandomWalk(setpoint=20.0, noise=0.0)

    reading = walk.next()

    assert reading == 20.0


def test_next_holds_steady_at_setpoint_with_no_noise_log_scale() -> None:
    walk = RandomWalk(setpoint=1e-7, noise=0.0, log_scale=True)

    reading = walk.next()

    assert reading == pytest.approx(1e-7)


def test_next_reverts_toward_setpoint_on_average_log_scale() -> None:
    walk = RandomWalk(setpoint=1e-7, noise=0.0, log_scale=True)
    walk.value = math.log10(1e-5)

    reading = walk.next()

    assert reading < 1e-5


def test_run_writes_points_and_shuts_down_cleanly_on_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeInfluxClient()
    monkeypatch.setattr(mock_sensor, "get_client", lambda: fake_client)

    registered_handlers: dict[int, SignalHandler] = {}

    def fake_signal(signalnum: int, handler: SignalHandler) -> None:
        registered_handlers[signalnum] = handler

    monkeypatch.setattr(signal, "signal", fake_signal)

    def fake_sleep(seconds: float) -> None:
        raise _StopLoop(seconds)

    monkeypatch.setattr(time, "sleep", fake_sleep)

    with pytest.raises(_StopLoop) as stop_info:
        run(sensor_id="test-sensor", interval=2.5, setpoint=10.0)
    assert stop_info.value.args == (2.5,)

    assert signal.SIGINT in registered_handlers
    assert signal.SIGTERM in registered_handlers

    shutdown_handler = registered_handlers[signal.SIGINT]
    with pytest.raises(SystemExit) as exit_info:
        shutdown_handler(signal.SIGINT, None)
    assert exit_info.value.code == 0

    assert fake_client.closed is True
    [batch] = fake_client.batches
    [point] = batch
    line = point.to_line_protocol()
    assert line.startswith("temperature,sensor_id=test-sensor value=")


def test_run_writes_points_with_custom_measurement_field_and_log_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeInfluxClient()
    monkeypatch.setattr(mock_sensor, "get_client", lambda: fake_client)

    registered_handlers: dict[int, SignalHandler] = {}

    def fake_signal(signalnum: int, handler: SignalHandler) -> None:
        registered_handlers[signalnum] = handler

    monkeypatch.setattr(signal, "signal", fake_signal)

    def fake_sleep(seconds: float) -> None:
        raise _StopLoop(seconds)

    monkeypatch.setattr(time, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        run(
            sensor_id="chamber-1",
            interval=1.0,
            setpoint=1e-7,
            measurement="pressure",
            field="reading",
            noise=0.05,
            log_scale=True,
            unit="mbar",
        )

    shutdown_handler = registered_handlers[signal.SIGINT]
    with pytest.raises(SystemExit):
        shutdown_handler(signal.SIGINT, None)

    [batch] = fake_client.batches
    [point] = batch
    line = point.to_line_protocol()
    assert line.startswith("pressure,sensor_id=chamber-1,unit=mbar reading=")


def test_main_parses_defaults_and_calls_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(mock_sensor, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["mock-sensor"])

    main()

    assert calls == [
        {
            "sensor_id": "mock-sensor-1",
            "interval": 5.0,
            "setpoint": 21.0,
            "measurement": "temperature",
            "field": "value",
            "noise": 0.1,
            "log_scale": False,
            "unit": "",
        }
    ]


def test_main_parses_custom_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(mock_sensor, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mock-sensor",
            "--sensor-id",
            "fridge-2",
            "--interval",
            "1",
            "--setpoint",
            "4",
        ],
    )

    main()

    assert calls == [
        {
            "sensor_id": "fridge-2",
            "interval": 1.0,
            "setpoint": 4.0,
            "measurement": "temperature",
            "field": "value",
            "noise": 0.1,
            "log_scale": False,
            "unit": "",
        }
    ]


def test_main_parses_pressure_gauge_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(mock_sensor, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mock-sensor",
            "--sensor-id",
            "chamber-1",
            "--measurement",
            "pressure",
            "--field",
            "reading",
            "--setpoint",
            "1e-7",
            "--noise",
            "0.05",
            "--log-scale",
            "--unit",
            "mbar",
        ],
    )

    main()

    assert calls == [
        {
            "sensor_id": "chamber-1",
            "interval": 5.0,
            "setpoint": 1e-7,
            "measurement": "pressure",
            "field": "reading",
            "noise": 0.05,
            "log_scale": True,
            "unit": "mbar",
        }
    ]
