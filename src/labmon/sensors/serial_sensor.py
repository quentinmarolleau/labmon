"""Reading a board over serial and converting counts to physical units.

`run()` is the loop: it reads raw counts from a `RawSource`, applies the
calibration for each channel, and writes the results. The command line
that drives it is `labmon.cli.commands.serial_sensor`.
"""

import logging

from labmon.calibration import (
    ADC_RESOLUTION_BITS,
    ADC_VREF_VOLTS,
    Calibration,
    raw_to_voltage,
    ureg,
    voltage_step,
)
from labmon.gate import RecordingGate
from labmon.influx import influx_database
from labmon.sensors.loop import DEFAULT_SUMMARY_INTERVAL_SECONDS, SensorLoop
from labmon.sensors.polling import build_point
from labmon.sensors.serial_source import (
    RawSource,
)

logger: logging.Logger = logging.getLogger(__name__)

# How many uncalibrated channels are named individually before the
# warnings give up. Comfortably above any real board — the reference
# sketch has six analog inputs — and low enough that the set cannot
# become a memory problem on a process left running for months.
MAX_WARNED_CHANNELS = 64

# INVARIANT (see docs/configuration.md): the three names below are the
# schema every stored reading was written under. Changing one splits the
# history in two — rows already in InfluxDB keep the old name, and no
# query spans both — so they are deliberately not configurable.

# The InfluxDB field every reading is written to. Unlike the mock
# sensor's --field, there's nothing to vary here: a channel's identity
# lives in its sensor_id tag, not in the field name.
FIELD_NAME = "value"

# The voltage a reading was converted from, written alongside the
# converted value unless the channel opts out (see Calibration.store_input).
# Named for its unit because the `unit` tag describes the converted value,
# not this.
INPUT_FIELD_NAME = "input_volts"

# Identifies which conversion produced a reading. A tag rather than a
# field so it can be filtered and grouped on; cardinality stays low
# because a calibration changes a handful of times over a sensor's life.
CALIBRATION_ID_TAG = "calibration_id"


def _log_calibrations(calibrations: dict[str, Calibration]) -> None:
    """Record which calibration each channel started with.

    The journal then says what was in force during a run, so a reading
    can be traced back even if the file has since been edited.
    """
    for channel, calibration in sorted(calibrations.items()):
        details = ", ".join(
            f"{key}={value!r}" for key, value in calibration.provenance.items()
        )
        logger.info(
            "calibration in force",
            extra={
                "channel": channel,
                "sensor_id": calibration.sensor_id,
                "calibration_id": calibration.calibration_id,
                "provenance": details or "-",
            },
        )


def run(
    source: RawSource,
    calibrations: dict[str, Calibration],
    resolution_bits: int = ADC_RESOLUTION_BITS,
    v_ref: float = ADC_VREF_VOLTS,
    summary_interval: float | None = DEFAULT_SUMMARY_INTERVAL_SECONDS,
) -> None:
    """Write calibrated readings from `source` to InfluxDB until interrupted.

    Runs until a SIGINT (Ctrl+C) or SIGTERM (e.g. `docker stop`) is
    received, at which point the serial port and InfluxDB client are
    both closed cleanly.
    """
    # The port is closed before the writer, so nothing new arrives while
    # the queue drains.
    loop = SensorLoop(
        closes=source,
        summary_interval=summary_interval,
        # Declared from the calibration file, so a channel that never
        # reports still appears in the summary as zero.
        sensors={calibration.sensor_id for calibration in calibrations.values()},
    )

    logger.info(
        "writing calibrated readings",
        extra={
            "channels": ",".join(sorted(calibrations)) or "-",
            "database": influx_database(),
        },
    )
    _log_calibrations(calibrations)

    # A board may stream channels this host has no calibration for;
    # warn once each rather than on every reading forever.
    #
    # Bounded, because remembering every channel seen is a leak if the
    # names never stop being novel. Parsing constrains a channel to 32
    # characters from a small alphabet, which rules out most line noise
    # but still leaves far more names than a board could legitimately
    # have. Past the cap the warnings stop naming channels; the readings
    # were already being discarded either way.
    warned_channels: set[str] = set()
    warned_channel_cap_reported = False

    # One gate per channel, since each carries its own deadband state:
    # sharing one would let an instrument being off silence a channel
    # that is still running. Channels without a `stop_recording_when`
    # table are absent here and record everything.
    gates = {
        channel: RecordingGate(calibration.stop_recording_when, calibration.sensor_id)
        for channel, calibration in calibrations.items()
        if calibration.stop_recording_when is not None
    }

    # One rounding filter per channel, built here because it depends on
    # the board's resolution as well as the channel's conversion, and
    # the board is only known once the command line has been read.
    step = voltage_step(resolution_bits, v_ref)
    rounding = {
        channel: calibration.rounding(step)
        for channel, calibration in calibrations.items()
    }

    while True:
        # `finally`, so the summary runs whatever this iteration did. Every
        # path below can `continue` — a read timeout, an uncalibrated
        # channel, a non-finite conversion, a closed gate — and skipping
        # the summary on those suppresses it exactly when it matters: a
        # silent board, a fully gated channel set and an all-uncalibrated
        # stream would each produce no output at all, leaving "working but
        # quiet" indistinguishable from "wedged". Keeping it at the end of
        # the iteration rather than the start means a summary still reports
        # the readings it covers, instead of lagging one behind.
        try:
            reading = source.read()
            if reading is None:
                continue

            calibration = calibrations.get(reading.channel)
            if calibration is None:
                if reading.channel not in warned_channels:
                    if len(warned_channels) < MAX_WARNED_CHANNELS:
                        warned_channels.add(reading.channel)
                        logger.warning(
                            "no calibration for channel; ignoring its readings",
                            extra={"channel": reading.channel},
                        )
                    elif not warned_channel_cap_reported:
                        warned_channel_cap_reported = True
                        logger.warning(
                            "too many uncalibrated channels; not naming any more",
                            extra={"limit": MAX_WARNED_CHANNELS},
                        )
                continue

            voltage = raw_to_voltage(reading.raw_count, resolution_bits, v_ref)
            value = calibration.conversion.apply(voltage)
            # Rounded before anything looks at it, so the gate compares
            # against the number that will actually be stored and the
            # log line quotes it too. `input_volts` below is left whole:
            # it is what a corrected calibration would be re-applied to,
            # and rounding it would make that irrecoverable.
            value = rounding[reading.channel](value.magnitude) * value.units

            # Before the gate, which compares against bounds: every comparison
            # with a NaN is False, so a gate would admit it and a gate-less
            # channel would never look at it at all.
            if not loop.admits(value.magnitude, sensor_id=calibration.sensor_id):
                continue

            gate = gates.get(reading.channel)
            if gate is not None and not gate.admits(value, voltage):
                # Nothing is written at all, not even input_volts: a
                # half-populated series is harder to read than a gap, and the
                # transition the gate logged is the evidence for the gap.
                continue

            # `build_point` owns the tag conventions and stamps the point.
            # The board has no clock, so the host stamps on arrival; serial
            # transit is negligible next to sample rates here.
            point = build_point(
                value.magnitude,
                sensor_id=calibration.sensor_id,
                measurement=calibration.measurement,
                unit=calibration.unit,
                field=FIELD_NAME,
                tags={CALIBRATION_ID_TAG: calibration.calibration_id},
            )
            # The second field is added here rather than by `build_point`,
            # which returns the point precisely so a caller with more to
            # record can add it without the builder growing a parameter
            # for every shape a sensor might have.
            if calibration.store_input:
                # Keeping the conversion's input makes a wrong calibration
                # correctable after the fact; without it, readings already
                # written can't be recomputed.
                point = point.field(INPUT_FIELD_NAME, voltage.to(ureg.volt).magnitude)
            loop.record(point, calibration.sensor_id)
            logger.debug(
                "reading",
                extra={
                    "sensor_id": calibration.sensor_id,
                    "value": f"{value.magnitude:.4g}",
                    "unit": calibration.unit,
                },
            )
        finally:
            loop.summarise_if_due()
