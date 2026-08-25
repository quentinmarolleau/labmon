import logging
import math
import signal
import time
from collections.abc import Callable
from types import FrameType

import pytest
from influxdb_client_3 import Point

from labmon.sensors import loop as sensor_loop
from labmon.sensors import mock_sensor
from labmon.sensors.mock_sensor import RandomWalk, run

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
    monkeypatch.setattr(sensor_loop, "get_client", lambda: fake_client)

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
    monkeypatch.setattr(sensor_loop, "get_client", lambda: fake_client)

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


def test_run_passes_its_summary_interval_to_the_loop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The flag is only worth parsing if it reaches the loop that uses it."""
    monkeypatch.setattr(sensor_loop, "get_client", FakeInfluxClient)

    def ignore_signal(_signum: int, _handler: SignalHandler) -> None:
        return None

    monkeypatch.setattr(signal, "signal", ignore_signal)
    # Well past the default interval, so a dropped argument would summarise.
    ticks = iter([0.0, sensor_loop.DEFAULT_SUMMARY_INTERVAL_SECONDS + 1.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))

    def fake_sleep(seconds: float) -> None:
        raise _StopLoop(seconds)

    monkeypatch.setattr(time, "sleep", fake_sleep)

    with (
        caplog.at_level(logging.INFO, logger=sensor_loop.logger.name),
        pytest.raises(_StopLoop),
    ):
        run(
            sensor_id="test-sensor",
            interval=1.0,
            setpoint=10.0,
            summary_interval=None,
        )

    assert "wrote readings" not in caplog.text


# --------------------------------------------------------------------------
# Reported resolution
#
# A simulated instrument that reports 76.85006139177405 K is claiming a
# precision no thermometer has. The walk itself stays at full precision —
# quantising its internal state would change how it reverts and drifts —
# so only the reported value is rounded.


def test_a_reading_is_rounded_to_the_resolution_step() -> None:
    assert mock_sensor.quantise(76.85006139177405, resolution=0.001) == 76.85


def test_a_step_that_is_not_a_power_of_ten_still_lands_on_the_grid() -> None:
    assert mock_sensor.quantise(76.85006139177405, resolution=0.25) == 76.75


def test_rounding_to_a_step_does_not_reintroduce_float_noise() -> None:
    # The arithmetic form, round(v / step) * step, gives
    # 76.85000000000001 here — putting back exactly the digits this is
    # meant to remove.
    rounded = mock_sensor.quantise(76.85006139177405, resolution=0.001)

    assert repr(rounded) == "76.85"


def test_a_reading_is_rounded_to_significant_digits_by_default() -> None:
    assert mock_sensor.quantise(76.85006139177405, significant_digits=4) == 76.85


def test_significant_digits_survive_a_reading_near_zero() -> None:
    # The reason this is the default: a vacuum gauge walks at 1e-7 mbar,
    # and an absolute step of 1e-3 would report every reading as zero.
    quantised = mock_sensor.quantise(5.594587307076873e-09, significant_digits=4)

    assert quantised == 5.595e-09


def test_an_explicit_resolution_wins_over_significant_digits() -> None:
    quantised = mock_sensor.quantise(
        76.85006139177405, resolution=0.001, significant_digits=4
    )

    assert quantised == 76.85


def test_quantising_leaves_a_non_finite_value_alone() -> None:
    # float("nan") never reaches a field — polling refuses it — but the
    # rounding primitive should not be the thing that raises.
    assert math.isnan(mock_sensor.quantise(float("nan"), resolution=0.001))


def test_the_walk_keeps_full_precision_internally() -> None:
    # Quantising the walk's own state would change how it reverts, and a
    # small enough step would freeze it entirely.
    walk = RandomWalk(setpoint=21.0, noise=0.5)

    for _ in range(20):
        _ = walk.next()

    assert walk.value != round(walk.value, 3)


def test_run_writes_readings_at_the_configured_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeInfluxClient()
    monkeypatch.setattr(sensor_loop, "get_client", lambda: fake_client)

    registered_handlers: dict[int, SignalHandler] = {}

    def fake_signal(signalnum: int, handler: SignalHandler) -> None:
        registered_handlers[signalnum] = handler

    monkeypatch.setattr(signal, "signal", fake_signal)

    readings = {"n": 0}

    def sleep_three_times(_seconds: float) -> None:
        readings["n"] += 1
        if readings["n"] >= 3:
            raise _StopLoop

    monkeypatch.setattr(time, "sleep", sleep_three_times)

    with pytest.raises(_StopLoop):
        run(
            sensor_id="cryo-77k",
            interval=0.0,
            setpoint=77.0,
            noise=0.3,
            unit="K",
            resolution=0.001,
            summary_interval=None,
        )

    shutdown_handler = registered_handlers[signal.SIGINT]
    with pytest.raises(SystemExit):
        shutdown_handler(signal.SIGINT, None)

    lines = [
        point.to_line_protocol() for batch in fake_client.batches for point in batch
    ]
    assert len(lines) == 3
    for line in lines:
        written = line.split("value=")[1].split()[0]
        _, _, decimals = written.partition(".")
        assert len(decimals) <= 3, line
