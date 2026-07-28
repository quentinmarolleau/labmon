import logging

import pytest
import serial

from labmon.sensors.serial_source import (
    RawReading,
    SerialRawSource,
    open_serial_port,
    parse_reading,
)


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
    "line",
    [
        pytest.param(b"garbage\n", id="no-separator"),
        pytest.param(b"A0,2048,extra\n", id="too-many-fields"),
        pytest.param(b"A0,not-a-number\n", id="non-numeric-count"),
        # float() would accept these where int() did not; a non-finite
        # count reaching InfluxDB poisons every aggregate over the series.
        pytest.param(b"A0,nan\n", id="nan-count"),
        pytest.param(b"A0,inf\n", id="inf-count"),
        pytest.param(b"A0,-inf\n", id="negative-inf-count"),
        pytest.param(b",2048\n", id="empty-channel"),
        pytest.param(b"\xff\xfe\n", id="undecodable-bytes"),
    ],
)
def test_parse_reading_skips_a_malformed_line(
    line: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        assert parse_reading(line) is None

    assert "Skipping malformed line" in caplog.text


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


def test_open_serial_port_configures_the_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_serial(*, port: str, baudrate: int, timeout: float) -> FakeSerialPort:
        captured["port"] = port
        captured["baudrate"] = baudrate
        captured["timeout"] = timeout
        return FakeSerialPort([])

    monkeypatch.setattr(serial, "Serial", fake_serial)

    _ = open_serial_port("/dev/labmon-due", baudrate=9600, timeout=2.5)

    assert captured == {
        "port": "/dev/labmon-due",
        "baudrate": 9600,
        "timeout": 2.5,
    }
