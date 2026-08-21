import logging

import pytest
import serial

from labmon.sensors.serial_source import (
    RawReading,
    SerialRawSource,
    open_serial_port,
    parse_reading,
)

# Quoted verbatim from the call site, so a reworded reason fails here
# rather than silently weakening the assertion to "some warning happened".
_EXPECTED_SHAPE = "expected '<channel>,<count>'"


def _field(record: logging.LogRecord, name: str) -> object:
    """Read a logfmt field off a record.

    `extra=` fields become attributes a type checker cannot know about,
    so this says plainly that the lookup is dynamic.
    """
    return getattr(record, name)  # pyright: ignore[reportAny]


class FakeSerialPort:
    """Stands in for a pyserial Serial, replaying canned lines."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines: list[bytes] = list(lines)
        self.closed: bool = False

    def readline(self) -> bytes:
        # An exhausted fake mimics a read timeout: no data, not an error.
        return self._lines.pop(0) if self._lines else b""

    def close(self) -> None:
        self.closed = True


def test_parse_reading_accepts_a_plain_line() -> None:
    assert parse_reading(b"A0,2048\n") == RawReading(channel="A0", raw_count=2048)


def test_parse_reading_accepts_the_crlf_arduino_println_emits() -> None:
    assert parse_reading(b"A0,2048\r\n") == RawReading(channel="A0", raw_count=2048)


def test_parse_reading_tolerates_surrounding_whitespace() -> None:
    assert parse_reading(b"  A0 , 2048  \r\n") == RawReading(
        channel="A0", raw_count=2048
    )


def test_parse_reading_keeps_a_fractional_count() -> None:
    # A board averaging several conversions per reading resolves below one
    # ADC step; rounding here would discard exactly that gain.
    assert parse_reading(b"A0,2048.31\r\n") == RawReading(
        channel="A0", raw_count=2048.31
    )


def test_parse_reading_accepts_scientific_notation() -> None:
    assert parse_reading(b"A0,2.04831e3\r\n") == RawReading(
        channel="A0", raw_count=2048.31
    )


def test_parse_reading_ignores_an_empty_read() -> None:
    # A read timeout yields no bytes; that is normal, not a malformed line.
    assert parse_reading(b"") is None


def test_parse_reading_ignores_a_blank_line() -> None:
    assert parse_reading(b"\r\n") is None


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        pytest.param(b"garbage\n", _EXPECTED_SHAPE, id="no-separator"),
        pytest.param(b"A0,2048,extra\n", _EXPECTED_SHAPE, id="too-many-fields"),
        pytest.param(b"A0,not-a-number\n", "count is not a number", id="non-numeric"),
        # float() would accept these where int() did not; a non-finite
        # count reaching InfluxDB poisons every aggregate over the series.
        pytest.param(b"A0,nan\n", "count is not finite", id="nan-count"),
        pytest.param(b"A0,inf\n", "count is not finite", id="inf-count"),
        pytest.param(b"A0,-inf\n", "count is not finite", id="negative-inf-count"),
        pytest.param(b",2048\n", "empty channel", id="empty-channel"),
        pytest.param(b"\xff\xfe\n", "not valid UTF-8", id="undecodable-bytes"),
    ],
)
def test_parse_reading_skips_a_malformed_line(
    line: bytes, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        assert parse_reading(line) is None

    # The message is a constant so every cause groups under one `msg`, and
    # `reason` is what tells them apart — which is the point of putting the
    # data in fields rather than in the sentence.
    [record] = caplog.records
    assert record.getMessage() == "skipping malformed line"
    assert _field(record, "reason") == reason
    assert _field(record, "line") == line


def test_read_returns_successive_readings() -> None:
    source = SerialRawSource(FakeSerialPort([b"A0,10\n", b"A1,20\n"]))

    assert source.read() == RawReading(channel="A0", raw_count=10)
    assert source.read() == RawReading(channel="A1", raw_count=20)


def test_read_returns_none_when_the_port_has_nothing_to_offer() -> None:
    assert SerialRawSource(FakeSerialPort([])).read() is None


def test_close_closes_the_underlying_port() -> None:
    port = FakeSerialPort([])
    source = SerialRawSource(port)

    source.close()

    assert port.closed


@pytest.mark.parametrize(
    "port",
    [
        pytest.param("/dev/labmon-due", id="device-path"),
        # A board on a serial device server rather than this machine.
        pytest.param("rfc2217://serial-server.lab:4001", id="rfc2217-url"),
        # Anything speaking the wire format over TCP; the demo stack uses
        # this to drive the real code path without hardware.
        pytest.param("socket://feeder:5555", id="socket-url"),
    ],
)
def test_open_serial_port_configures_the_device(
    monkeypatch: pytest.MonkeyPatch, port: str
) -> None:
    captured: dict[str, object] = {}

    def fake_serial_for_url(
        url: str, *, baudrate: int, timeout: float
    ) -> FakeSerialPort:
        captured["port"] = url
        captured["baudrate"] = baudrate
        captured["timeout"] = timeout
        return FakeSerialPort([])

    monkeypatch.setattr(serial, "serial_for_url", fake_serial_for_url)

    _ = open_serial_port(port, baudrate=9600, timeout=2.5)

    assert captured == {"port": port, "baudrate": 9600, "timeout": 2.5}
