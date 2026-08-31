import logging
import signal
import time
from collections.abc import Callable
from types import FrameType

import pint
import pytest
from influxdb_client_3 import Point

from labmon.calibration import Calibration, LinearConversion, ureg
from labmon.gate import StopRecordingRule
from labmon.sensors import loop as sensor_loop
from labmon.sensors import serial_sensor
from labmon.sensors.serial_sensor import MAX_WARNED_CHANNELS, run
from labmon.sensors.serial_source import RawReading

SignalHandler = Callable[[int, FrameType | None], None]


def _field(record: logging.LogRecord, name: str) -> object:
    """Read a logfmt field off a record.

    `extra=` fields become attributes a type checker cannot know about,
    so this says plainly that the lookup is dynamic.
    """
    return getattr(record, name)  # pyright: ignore[reportAny]


class _StopLoop(Exception):
    pass


def _clock(*ticks: float) -> Callable[[], float]:
    """A `time.monotonic` stand-in that holds its final value.

    These tests care that the clock crosses the summary interval, not how
    many times the loop happens to read it. A plain iterator pins the call
    count, so an unrelated change to the loop body fails here with a
    `StopIteration` that says nothing about what broke.
    """
    values = list(ticks)

    def read() -> float:
        return values.pop(0) if len(values) > 1 else values[0]

    return read


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


class _UndefinedBelowMidscale:
    """A conversion with no value over part of its range.

    Stands in for the reported case, `log(v - 2.0)`, without needing an
    asteval interpreter — and makes the point that the guard is not
    specific to expression conversions. A spline through awkward points or
    an affine factor large enough to overflow arrives the same way.
    """

    def apply(self, voltage: pint.Quantity, /) -> pint.Quantity:
        volts = voltage.to(ureg.volt).magnitude
        return (float("nan") if volts < 1.65 else 42.5 * volts) * ureg.kelvin

    def resolution(self, _voltage_step: pint.Quantity, /) -> None:
        return None

    def fingerprint(self) -> str:
        return "test|undefined-below-midscale"


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
    monkeypatch.setattr(sensor_loop, "get_client", lambda: client)
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
    # sorts tags and fields alphabetically.
    assert line.startswith("temperature,calibration_id=")
    assert ",sensor_id=cryo-77k,unit=K " in line
    assert "value=140.2" in line
    assert "input_volts=3.3" in line


def test_run_writes_no_unit_tag_for_a_dimensionless_channel(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    """A ratio has no unit, and an empty unit tag is not the same as none.

    This is the convention `build_point` exists to own. `serial-sensor`
    used to tag `unit` unconditionally, and only the client's habit of
    dropping an empty tag kept the two spellings from splitting the
    series in two — halves that would look identical in a legend. Pinned
    here so the guarantee rests on this repository rather than on a
    detail of influxdb_client_3.
    """
    source = FakeRawSource([RawReading(channel="A0", raw_count=4095)])
    ratio = Calibration(
        sensor_id="beam-ratio",
        measurement="ratio",
        conversion=LinearConversion(factor=ureg("0.3 / volt")),
    )
    assert ratio.unit == "", "a dimensionless conversion should carry no unit"

    with pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": ratio})
    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    [batch] = fake_client.batches
    [point] = batch
    line = point.to_line_protocol()
    assert "sensor_id=beam-ratio" in line
    assert "unit=" not in line


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


def test_run_tags_readings_with_the_calibration_that_produced_them(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    calibration = _temperature_calibration()
    source = FakeRawSource([RawReading(channel="A0", raw_count=4095)])

    with pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": calibration})

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    [batch] = fake_client.batches
    [point] = batch
    assert f"calibration_id={calibration.calibration_id}" in point.to_line_protocol()


@pytest.mark.usefixtures("fake_client", "registered_handlers")
def test_run_logs_the_calibration_and_provenance_of_every_channel(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The journal is the secondary record of what was in force during a
    # run, for when the file has since been edited.
    calibration = Calibration(
        sensor_id="cryo-77k",
        measurement="temperature",
        conversion=LinearConversion(factor=ureg("42.5 kelvin / volt")),
        provenance={"date": "2026-07-28", "reference": "Lakeshore 336"},
    )

    with caplog.at_level(logging.INFO), pytest.raises(_StopLoop):
        run(source=FakeRawSource([]), calibrations={"A0": calibration})

    [record] = [r for r in caplog.records if r.getMessage() == "calibration in force"]
    assert _field(record, "calibration_id") == calibration.calibration_id
    assert _field(record, "sensor_id") == "cryo-77k"
    assert "Lakeshore 336" in str(_field(record, "provenance"))


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


@pytest.mark.usefixtures("fake_client")
def test_run_still_summarises_when_the_board_says_nothing(
    monkeypatch: pytest.MonkeyPatch,
    registered_handlers: dict[int, SignalHandler],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A silent board is the case the summary exists to report on.

    `None` is what a read timeout looks like. If the summary only runs on
    an iteration that wrote something, a board that has stopped talking
    produces no output at all — and "working but quiet" becomes
    indistinguishable from "wedged", which is the one distinction this
    line is for.
    """
    monkeypatch.setattr(
        time,
        "monotonic",
        _clock(0.0, sensor_loop.DEFAULT_SUMMARY_INTERVAL_SECONDS + 1.0),
    )
    source = FakeRawSource([None, None])

    with (
        caplog.at_level(logging.INFO, logger=sensor_loop.logger.name),
        pytest.raises(_StopLoop),
    ):
        run(source=source, calibrations={"A0": _temperature_calibration()})

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    # Nothing was written, and the summary says exactly that rather than
    # being absent.
    summaries = [r for r in caplog.records if r.getMessage() == "wrote readings"]
    assert summaries != [], "a silent source produced no summary at all"


def test_run_skips_a_non_finite_conversion_and_keeps_the_rest(
    fake_client: FakeInfluxClient,
    registered_handlers: dict[int, SignalHandler],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The transient case: valid, two undefined, valid again.

    A conversion undefined at one end of its range says nothing about the
    rest, so the readings either side are still written. Only the two that
    have no value are dropped, and one line says so.
    """
    calibration = Calibration(
        sensor_id="cryo-diode",
        measurement="temperature",
        conversion=_UndefinedBelowMidscale(),
    )
    source = FakeRawSource(
        [
            RawReading(channel="A0", raw_count=4095),
            RawReading(channel="A0", raw_count=0),
            RawReading(channel="A0", raw_count=100),
            RawReading(channel="A0", raw_count=4095),
        ]
    )

    with caplog.at_level(logging.WARNING), pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": calibration})

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    written = [point for batch in fake_client.batches for point in batch]
    assert len(written) == 2
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert _field(warnings[0], "sensor_id") == "cryo-diode"


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
    warnings = [
        r
        for r in caplog.records
        if r.getMessage() == "no calibration for channel; ignoring its readings"
    ]
    assert len(warnings) == 1
    assert _field(warnings[0], "channel") == "A7"
    [batch] = fake_client.batches
    assert len(batch) == 1


def test_run_stops_tracking_uncalibrated_channels_past_the_cap(
    fake_client: FakeInfluxClient,
    registered_handlers: dict[int, SignalHandler],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The warned-channel set must not grow with the input forever.

    Warning once per channel means remembering every channel seen. A
    board — or line noise that parses — producing endlessly novel names
    would otherwise grow that set for as long as the process runs, which
    on a sensor left up for months is a leak.
    """
    novel: list[RawReading | None] = [
        RawReading(channel=f"ch{index}", raw_count=1)
        for index in range(MAX_WARNED_CHANNELS + 5)
    ]
    source = FakeRawSource(novel)

    with caplog.at_level(logging.WARNING), pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": _temperature_calibration()})

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    per_channel = [
        r
        for r in caplog.records
        if r.getMessage() == "no calibration for channel; ignoring its readings"
    ]
    assert len(per_channel) == MAX_WARNED_CHANNELS

    # And it says so once, rather than going quiet and leaving the
    # operator to wonder why the warnings stopped.
    [capped] = [
        r
        for r in caplog.records
        if r.getMessage() == "too many uncalibrated channels; not naming any more"
    ]
    assert _field(capped, "limit") == MAX_WARNED_CHANNELS

    # None of it was recorded: an uncalibrated channel has no conversion,
    # so there is nothing to write whether or not it was warned about.
    assert fake_client.batches == []


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


@pytest.mark.usefixtures("fake_client", "registered_handlers")
def test_per_reading_lines_are_debug_not_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """At 100 Hz an INFO line per reading buries every warning worth seeing."""
    source = FakeRawSource([RawReading(channel="A0", raw_count=4095)])

    with (
        caplog.at_level(logging.DEBUG, logger=serial_sensor.logger.name),
        pytest.raises(_StopLoop),
    ):
        run(source=source, calibrations={"A0": _temperature_calibration()})

    reading_lines = [r for r in caplog.records if r.getMessage() == "reading"]
    assert [r.levelno for r in reading_lines] == [logging.DEBUG]
    # The value travels as a field, not inside the sentence — which is
    # what lets a collector pick it out.
    assert _field(reading_lines[0], "sensor_id") == "cryo-77k"
    assert str(_field(reading_lines[0], "value")).startswith("140.2")


@pytest.mark.usefixtures("fake_client", "registered_handlers")
def test_a_summary_is_logged_once_the_interval_elapses(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-reading line moved to DEBUG, so this carries "it is working"."""
    # Two readings: the clock jumps past the interval only for the second,
    # so exactly one summary covering both should be emitted. Patched on
    # the time module itself, which serial_sensor imports rather than
    # re-exports.
    monkeypatch.setattr(
        time,
        "monotonic",
        _clock(0.0, 1.0, sensor_loop.DEFAULT_SUMMARY_INTERVAL_SECONDS + 1.0),
    )
    source = FakeRawSource(
        [
            RawReading(channel="A0", raw_count=4095),
            RawReading(channel="A0", raw_count=4095),
        ]
    )

    with (
        caplog.at_level(logging.INFO, logger=sensor_loop.logger.name),
        pytest.raises(_StopLoop),
    ):
        run(source=source, calibrations={"A0": _temperature_calibration()})

    summaries = [r for r in caplog.records if r.getMessage() == "wrote readings"]
    assert [(_field(r, "sensor_id"), _field(r, "readings")) for r in summaries] == [
        ("cryo-77k", 2)
    ]


@pytest.mark.usefixtures("fake_client", "registered_handlers")
def test_run_passes_its_summary_interval_to_the_loop(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flag is only worth parsing if it reaches the loop that uses it."""
    # Well past the default interval, so a dropped argument would summarise.
    ticks = iter([0.0, sensor_loop.DEFAULT_SUMMARY_INTERVAL_SECONDS + 1.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    source = FakeRawSource([RawReading(channel="A0", raw_count=4095)])

    with (
        caplog.at_level(logging.INFO, logger=sensor_loop.logger.name),
        pytest.raises(_StopLoop),
    ):
        run(
            source=source,
            calibrations={"A0": _temperature_calibration()},
            summary_interval=None,
        )

    assert "wrote readings" not in caplog.text


# --------------------------------------------------------------------------
# the recording gate
# --------------------------------------------------------------------------


def _gated_calibration(dwell_seconds: float = 0.0) -> Calibration:
    """A photodiode reading 42.5 mW per volt, stopping below 1 mW."""
    return Calibration(
        sensor_id="laser-1",
        measurement="power",
        conversion=LinearConversion(factor=ureg("42.5 mW / volt")),
        stop_recording_when=StopRecordingRule(
            below=ureg("1.0 mW"), dwell_seconds=dwell_seconds
        ),
    )


def test_a_gated_out_reading_is_not_written(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    # Count 1 is ~0.8 mV, so ~0.035 mW — well below the 1 mW bound.
    source = FakeRawSource([RawReading(channel="A0", raw_count=1)])

    with pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": _gated_calibration()})

    shutdown = registered_handlers[signal.SIGINT]
    with pytest.raises(SystemExit):
        shutdown(signal.SIGINT, None)

    assert fake_client.batches == []


def test_a_reading_inside_the_band_still_reaches_influx(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    source = FakeRawSource([RawReading(channel="A0", raw_count=4095)])

    with pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": _gated_calibration()})

    shutdown = registered_handlers[signal.SIGINT]
    with pytest.raises(SystemExit):
        shutdown(signal.SIGINT, None)

    [batch] = fake_client.batches
    assert len(batch) == 1


def test_a_gated_channel_leaves_an_ungated_one_alone(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    """The gate is per channel, so one instrument going off silences only itself."""
    source = FakeRawSource(
        [
            RawReading(channel="A0", raw_count=1),
            RawReading(channel="A1", raw_count=1),
        ]
    )

    with pytest.raises(_StopLoop):
        run(
            source=source,
            calibrations={
                "A0": _gated_calibration(),
                "A1": _temperature_calibration(),
            },
        )

    shutdown = registered_handlers[signal.SIGINT]
    with pytest.raises(SystemExit):
        shutdown(signal.SIGINT, None)

    [batch] = fake_client.batches
    [point] = batch
    assert "sensor_id=cryo-77k" in point.to_line_protocol()


@pytest.mark.usefixtures("fake_client")
def test_a_gated_out_reading_is_not_counted_in_the_summary(
    registered_handlers: dict[int, SignalHandler],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The summary reports what was written; a gap must not read as activity."""
    monkeypatch.setattr(sensor_loop, "DEFAULT_SUMMARY_INTERVAL_SECONDS", 0.0)
    source = FakeRawSource([RawReading(channel="A0", raw_count=1)])

    with caplog.at_level(logging.INFO), pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": _gated_calibration()})

    shutdown = registered_handlers[signal.SIGINT]
    with pytest.raises(SystemExit):
        shutdown(signal.SIGINT, None)

    assert [
        record for record in caplog.records if record.message == "wrote readings"
    ] == []


@pytest.mark.usefixtures("fake_client")
def test_the_transition_names_the_sensor_that_stopped(
    registered_handlers: dict[int, SignalHandler],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The log line is what makes the gap in the trace interpretable."""
    source = FakeRawSource([RawReading(channel="A0", raw_count=1)])

    with caplog.at_level(logging.INFO), pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": _gated_calibration()})

    shutdown = registered_handlers[signal.SIGINT]
    with pytest.raises(SystemExit):
        shutdown(signal.SIGINT, None)

    [stopped] = [r for r in caplog.records if r.message == "recording stopped"]
    assert _field(stopped, "sensor_id") == "laser-1"


def test_a_raw_voltage_gate_sees_the_voltage_the_loop_measured(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    """Count 4095 is 3.3 V, which is outside the band whatever it converts to."""
    source = FakeRawSource([RawReading(channel="A0", raw_count=4095)])
    calibration = Calibration(
        sensor_id="quadrant-1",
        measurement="position",
        conversion=LinearConversion(factor=ureg("42.5 mW / volt")),
        stop_recording_when=StopRecordingRule(above=ureg("3.0 V"), raw_voltage=True),
    )

    with pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": calibration})

    shutdown = registered_handlers[signal.SIGINT]
    with pytest.raises(SystemExit):
        shutdown(signal.SIGINT, None)

    assert fake_client.batches == []


def test_each_channel_gets_its_own_gate_state(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    """Two gated channels sharing one gate would have one silence the other."""
    source = FakeRawSource(
        [
            RawReading(channel="A0", raw_count=1),
            RawReading(channel="A1", raw_count=4095),
        ]
    )

    with pytest.raises(_StopLoop):
        run(
            source=source,
            calibrations={"A0": _gated_calibration(), "A1": _gated_calibration()},
        )

    shutdown = registered_handlers[signal.SIGINT]
    with pytest.raises(SystemExit):
        shutdown(signal.SIGINT, None)

    [batch] = fake_client.batches
    assert len(batch) == 1


# --------------------------------------------------------------------------
# What reaches the database
# --------------------------------------------------------------------------


def test_a_stored_reading_carries_only_the_digits_the_input_had(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    # 70.14212454212455 K claims seventeen digits of a twelve-bit
    # measurement. The input steps by 806 uV, which through 42.5 K/V is
    # 34 mK, so hundredths of a kelvin is what was actually measured.
    source = FakeRawSource([RawReading(channel="A0", raw_count=2048)])

    with pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": _temperature_calibration()})

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    [batch] = fake_client.batches
    [point] = batch
    assert "value=70.14 " in point.to_line_protocol()


def test_the_conversion_input_keeps_every_digit_it_had(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    # `input_volts` is what a corrected calibration would be re-applied
    # to. Rounding it would make that irrecoverable, which is the whole
    # reason the field is stored.
    source = FakeRawSource([RawReading(channel="A0", raw_count=2048)])

    with pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": _temperature_calibration()})

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    [batch] = fake_client.batches
    [point] = batch
    assert "input_volts=1.6504029304029304" in point.to_line_protocol()


def test_a_coarser_board_stores_coarser_readings(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    # Ten bits over 5 V steps by 4.9 mV, which through 42.5 K/V is
    # 0.21 K — a whole decimal place coarser than the 12-bit default.
    source = FakeRawSource([RawReading(channel="A0", raw_count=512)])

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
    assert "value=106.4 " in point.to_line_protocol()


def test_a_channel_may_state_its_own_digits(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    # A board averaging a burst of conversions resolves below one ADC
    # step, and the host cannot see how many it averaged.
    calibration = Calibration(
        sensor_id="cryo-77k",
        measurement="temperature",
        conversion=LinearConversion(factor=ureg("42.5 kelvin / volt")),
        significant_digits=7,
    )
    source = FakeRawSource([RawReading(channel="A0", raw_count=2048)])

    with pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": calibration})

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    [batch] = fake_client.batches
    [point] = batch
    assert "value=70.14212 " in point.to_line_protocol()


def test_the_gate_compares_against_the_reading_that_will_be_stored(
    fake_client: FakeInfluxClient, registered_handlers: dict[int, SignalHandler]
) -> None:
    # 70.14212454212455 K rounds to 70.14, which is under the bound the
    # unrounded value sits over. A gate reading one number while the
    # database keeps another would be unexplainable from the data.
    calibration = Calibration(
        sensor_id="cryo-77k",
        measurement="temperature",
        conversion=LinearConversion(factor=ureg("42.5 kelvin / volt")),
        stop_recording_when=StopRecordingRule(above=ureg("70.141 kelvin")),
    )
    source = FakeRawSource([RawReading(channel="A0", raw_count=2048)])

    with pytest.raises(_StopLoop):
        run(source=source, calibrations={"A0": calibration})

    with pytest.raises(SystemExit):
        registered_handlers[signal.SIGINT](signal.SIGINT, None)

    [batch] = fake_client.batches
    [point] = batch
    assert "value=70.14 " in point.to_line_protocol()
