"""Reading `.env` for a command typed at a prompt.

Compose reads `.env`; a shell does not. Everything here is about that
gap, and about the two ways closing it could go wrong: overriding a
variable somebody set deliberately, and reading a file they did not mean
to point at.
"""

import logging
import os
from pathlib import Path

import pytest

from labmon import env

# Two fake tokens rather than one, so an assertion says which source won
# rather than only that something was set. Named constants rather than
# literals at the comparison, which ruff's S105 reads as a hardcoded
# credential — rightly, since it cannot tell these from a real one.
_IN_THE_FILE = "apiv3_from_the_file"
_IN_THE_SHELL = "apiv3_from_the_shell"


def test_no_file_means_nothing_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory with no `.env` is the normal case, not an error."""
    monkeypatch.chdir(tmp_path)
    before = dict(os.environ)

    assert env.load() is None
    assert dict(os.environ) == before


def test_a_setting_only_in_the_file_reaches_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INFLUXDB3_AUTH_TOKEN", raising=False)
    _ = (tmp_path / ".env").write_text(f"INFLUXDB3_AUTH_TOKEN={_IN_THE_FILE}\n")

    assert env.load() == tmp_path / ".env"
    assert os.environ["INFLUXDB3_AUTH_TOKEN"] == _IN_THE_FILE


def test_the_environment_wins_over_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A container, a systemd unit and a `VAR=x labmon …` prefix all set
    the variable themselves, and none of them should change behaviour
    because a file happens to sit in the working directory."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INFLUXDB3_AUTH_TOKEN", _IN_THE_SHELL)
    _ = (tmp_path / ".env").write_text(f"INFLUXDB3_AUTH_TOKEN={_IN_THE_FILE}\n")

    _ = env.load()

    assert os.environ["INFLUXDB3_AUTH_TOKEN"] == _IN_THE_SHELL


def test_a_key_with_no_value_is_not_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`dotenv` reports a bare `KEY` line as None.

    Setting it would put an empty string in the environment, and
    `_setting()` treats empty as unset anyway — so the only thing it
    could achieve is masking a value inherited from the parent process.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INFLUXDB_DATABASE", "inherited")
    _ = (tmp_path / ".env").write_text("BARE_KEY\n")

    _ = env.load()

    assert "BARE_KEY" not in os.environ
    assert os.environ["INFLUXDB_DATABASE"] == "inherited"


def test_the_file_it_read_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The whole point of announcing it.

    Two checkouts side by side, a command run in the wrong one, and
    readings written to the wrong database — silently, unless the file
    that configured the run is named.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INFLUXDB_HOST", raising=False)
    _ = (tmp_path / ".env").write_text("INFLUXDB_HOST=http://elsewhere:8181\n")

    with caplog.at_level(logging.INFO, logger="labmon.env"):
        _ = env.load()

    # The path rides in `extra`, which reaches the line through
    # LogfmtFormatter rather than through the record's message — so the
    # attribute is what the assertion can see here.
    (record,) = caplog.records
    assert record.getMessage() == "read environment file"
    assert getattr(record, "path", None) == str(tmp_path / ".env")


def test_a_file_that_changed_nothing_is_not_announced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Every variable already set means the file decided nothing.

    Which is the normal case for anyone using the `.envrc` beside it, so
    an INFO line there would be noise on every command they run. It
    stays at DEBUG rather than disappearing, because "which file did it
    look at" is still a question worth being able to answer.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INFLUXDB_HOST", "http://from-the-shell:8181")
    _ = (tmp_path / ".env").write_text("INFLUXDB_HOST=http://elsewhere:8181\n")

    with caplog.at_level(logging.INFO, logger="labmon.env"):
        _ = env.load()
    assert caplog.records == []

    with caplog.at_level(logging.DEBUG, logger="labmon.env"):
        _ = env.load()
    assert len(caplog.records) == 1


def test_nothing_is_logged_when_there_is_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Most invocations are somewhere without one, and say nothing."""
    monkeypatch.chdir(tmp_path)

    with caplog.at_level(logging.INFO, logger="labmon.env"):
        _ = env.load()

    assert caplog.text == ""


def test_only_the_working_directory_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No walking upwards.

    A `.env` three levels up belongs to something else, and letting it
    configure a sensor is a worse surprise than the convenience is
    worth.
    """
    _ = (tmp_path / ".env").write_text(f"INFLUXDB3_AUTH_TOKEN={_IN_THE_FILE}\n")
    below = tmp_path / "checkout"
    below.mkdir()
    monkeypatch.chdir(below)
    monkeypatch.delenv("INFLUXDB3_AUTH_TOKEN", raising=False)

    assert env.load() is None
    assert "INFLUXDB3_AUTH_TOKEN" not in os.environ


def test_a_directory_named_env_is_not_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Docker creates one of these when a bind mount source is missing."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").mkdir()

    assert env.load() is None
