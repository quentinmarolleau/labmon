"""Read raw sensor counts from a serial device.

The wire format is deliberately plain ASCII, one reading per line:

    <channel>,<raw_count>\\n

No throughput here justifies a binary framing, and plain text stays
debuggable with nothing more than `screen` or `minicom` when a board
misbehaves.

The count may be fractional: a board that averages several conversions
per reading reports a mean, and rounding it back to a whole count would
discard the sub-LSB resolution that averaging buys. Whole counts parse
just as well, so a board that sends one snapshot per reading needs no
change.

Nothing in this module knows what a count *means* — turning one into a
physical quantity is `labmon.calibration`'s job.
"""

import logging
import math
from dataclasses import dataclass
from typing import Protocol

import serial

logger: logging.Logger = logging.getLogger(__name__)

_FIELD_SEPARATOR = ","
_FIELDS_PER_LINE = 2

# Long enough not to spin, short enough that a stop request is noticed
# promptly: read() returns None on timeout, so the caller's loop keeps
# ticking even while the device is silent.
DEFAULT_READ_TIMEOUT_SECONDS = 1.0

# Ignored by an Arduino Due's native USB port (CDC runs at full USB
# speed regardless), but pyserial still requires a value.
DEFAULT_BAUDRATE = 115200


@dataclass(frozen=True)
class RawReading:
    """One uncalibrated sample: which channel, and what count it reported."""

    channel: str
    raw_count: float


class SerialPort(Protocol):
    """The slice of pyserial's Serial that this module depends on."""

    def readline(self) -> bytes: ...
    def close(self) -> None: ...


class RawSource(Protocol):
    """A source of raw readings, serial-backed or otherwise."""

    def read(self) -> RawReading | None: ...
    def close(self) -> None: ...


def parse_reading(line: bytes) -> RawReading | None:
    """Parse one wire-format line, or None if there isn't a reading in it.

    Returns None both for "nothing arrived" (an empty read, which is what
    a timeout looks like) and for a malformed line, which is logged and
    skipped: one bad line from a noisy connection shouldn't take the
    process down.
    """
    try:
        text = line.decode("utf-8").strip()
    except UnicodeDecodeError:
        logger.warning("Skipping malformed line (not valid UTF-8): %r", line)
        return None

    if not text:
        return None

    fields = [field.strip() for field in text.split(_FIELD_SEPARATOR)]
    if len(fields) != _FIELDS_PER_LINE:
        logger.warning(
            "Skipping malformed line (expected '<channel>,<count>'): %r", line
        )
        return None

    channel, raw_count = fields
    if not channel:
        logger.warning("Skipping malformed line (empty channel): %r", line)
        return None

    try:
        count = float(raw_count)
    except ValueError:
        logger.warning("Skipping malformed line (count is not a number): %r", line)
        return None

    # float() accepts "nan" and "inf", which would otherwise reach InfluxDB
    # and poison every aggregate computed over the series.
    if not math.isfinite(count):
        logger.warning("Skipping malformed line (count is not finite): %r", line)
        return None

    return RawReading(channel=channel, raw_count=count)


def open_serial_port(
    port: str,
    baudrate: int = DEFAULT_BAUDRATE,
    timeout: float = DEFAULT_READ_TIMEOUT_SECONDS,
) -> SerialPort:
    """Open a serial device, ready to hand to SerialRawSource."""
    return serial.Serial(port=port, baudrate=baudrate, timeout=timeout)


class SerialRawSource:
    """Reads raw readings line by line from an open serial port.

    Takes an already-open port rather than opening one itself, mirroring
    how PointWriter takes an already-built client — it keeps the device
    setup (open_serial_port) separate from the reading loop, so tests can
    drive this with a fake port and no hardware.
    """

    def __init__(self, port: SerialPort) -> None:
        self._port: SerialPort = port

    def read(self) -> RawReading | None:
        """Return the next reading, or None if none was available."""
        return parse_reading(self._port.readline())

    def close(self) -> None:
        """Close the underlying serial port."""
        self._port.close()
