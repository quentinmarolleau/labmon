import logging

import pytest

from labmon.logs import (
    LEVEL_NAMES,
    LogfmtFormatter,
    configure,
    level_from_name,
    quote,
)


def _record(message: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="labmon.test",
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# --------------------------------------------------------------------------
# quoting
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plain", "plain"),
        ("cryo-77k", "cryo-77k"),
        (42, "42"),
        (1.5, "1.5"),
        # A space would otherwise split one field into two.
        ("two words", '"two words"'),
        # A bare `=` reads as the start of another field.
        ("a=b", '"a=b"'),
        ("", '""'),
        ('say "hi"', '"say \\"hi\\""'),
        ("line\nbreak", '"line\\nbreak"'),
        ("back\\slash", "back\\slash"),
        # A tab used to force quoting and then survive inside the quotes.
        ("a\tb", '"a\\tb"'),
        # CR returns a terminal's cursor to the start of the line, so the
        # tail of a value overwrites what was already printed.
        ("over\rwrite", '"over\\rwrite"'),
        ("crlf\r\nline", '"crlf\\r\\nline"'),
        # No short spelling, so these fall through to a hex escape. ESC is
        # the one that matters: it introduces the ANSI sequences.
        ("\x1b[31mred", '"\\x1b[31mred"'),
        ("ring\x07ing", '"ring\\x07ing"'),
        ("ab\x08\x08cd", '"ab\\x08\\x08cd"'),
        ("a\x00b", '"a\\x00b"'),
        # Reorders a line without altering a byte of its content.
        ("start\u202eend", '"start\\u202eend"'),
        # Printable is printable, whatever its codepoint.
        ("20\u00b0C", "20\u00b0C"),
        ("\u00b5m", "\u00b5m"),
        ("\u03a9", "\u03a9"),
    ],
)
def test_quote_only_when_it_must(value: object, expected: str) -> None:
    assert quote(value) == expected


@pytest.mark.parametrize(
    "control",
    [
        "\n",
        "\r",
        "\r\n",
        "\t",
        "\x00",
        "\x07",
        "\x08",
        "\x0b",
        "\x0c",
        "\x1b",
        "\x7f",
        "\u202e",
        "\u0085",
    ],
)
def test_no_control_character_survives_quoting(control: str) -> None:
    """Whatever a device puts on the wire, the token stays one flat line.

    Channel names come off the serial port and a malformed-line warning
    carries the raw line, so these bytes are not always ours to choose.
    """
    rendered = quote(f"before{control}after")

    assert control not in rendered
    assert rendered.startswith('"') and rendered.endswith('"')
    assert all(character.isprintable() for character in rendered)


@pytest.mark.parametrize("control", ["\n", "\r", "\x1b", "\x08"])
def test_a_control_character_cannot_forge_a_field(control: str) -> None:
    """The attack the quoting exists to stop, in each of its spellings."""
    rendered = quote(f"ok{control}level=error msg=fake")

    assert control not in rendered
    assert rendered.count("=") == 2
    assert rendered.startswith('"') and rendered.endswith('"')


def test_an_astral_non_printable_is_escaped_too() -> None:
    """Above the BMP the escape widens rather than truncating."""
    # A tag character: invisible, and above U+FFFF.
    assert quote("a\U000e0041b") == '"a\\U000e0041b"'


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------


def test_a_line_carries_level_logger_and_message() -> None:
    line = LogfmtFormatter().format(_record("writing readings"))

    assert "level=info" in line
    assert "logger=labmon.test" in line
    assert 'msg="writing readings"' in line
    assert line.startswith("ts=")


def test_extras_become_fields() -> None:
    line = LogfmtFormatter().format(
        _record("reading", sensor_id="cryo-77k", value=77.3)
    )

    assert "sensor_id=cryo-77k" in line
    assert "value=77.3" in line


def test_fields_are_ordered_so_lines_stay_diffable() -> None:
    """A given call site should always render its fields the same way."""
    line = LogfmtFormatter().format(_record("m", zebra=1, alpha=2, middle=3))

    assert line.index("alpha=") < line.index("middle=") < line.index("zebra=")


def test_a_field_needing_quotes_gets_them() -> None:
    line = LogfmtFormatter().format(_record("m", provenance="date='2026-07-28'"))

    assert "provenance=\"date='2026-07-28'\"" in line


def test_a_traceback_follows_the_fields_rather_than_hiding_in_one() -> None:
    """Mangling it into a quoted value makes it unreadable when needed."""
    try:
        raise ValueError("device busy")
    except ValueError:
        import sys

        record = _record("read failed")
        record.exc_info = sys.exc_info()

    line = LogfmtFormatter().format(record)
    first, _, traceback = line.partition("\n")

    assert 'msg="read failed"' in first
    assert "ValueError: device busy" in traceback


def test_configure_installs_the_formatter(capsys: pytest.CaptureFixture[str]) -> None:
    configure(logging.DEBUG)
    logging.getLogger("labmon.test").debug("hello", extra={"sensor_id": "s"})

    captured = capsys.readouterr().err
    assert "level=debug" in captured
    assert "msg=hello sensor_id=s" in captured


# --------------------------------------------------------------------------
# --log-level must fail loudly on a typo
# --------------------------------------------------------------------------


def test_every_advertised_level_resolves() -> None:
    """The choices argparse offers must all be levels logging knows."""
    assert [level_from_name(name) for name in LEVEL_NAMES] == [
        logging.DEBUG,
        logging.INFO,
        logging.WARNING,
        logging.ERROR,
        logging.CRITICAL,
    ]


def test_a_level_name_is_case_insensitive() -> None:
    assert level_from_name("debug") == logging.DEBUG


def test_an_unknown_level_name_raises_rather_than_defaulting() -> None:
    """Silently falling back to INFO hides the typo that caused it."""
    with pytest.raises(KeyError):
        _ = level_from_name("verbose")
