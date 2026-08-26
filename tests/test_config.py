"""The user configuration file: where it lives, and what it accepts."""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from labmon.config import ConfigError, config_path, load


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "labmon" / "labmon.toml"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text)
    return path


def test_the_path_honours_xdg_config_home(config_home: Path) -> None:
    assert config_path() == config_home


def test_the_path_falls_back_to_dot_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/somebody")

    assert config_path() == Path("/home/somebody/.config/labmon/labmon.toml")


def test_a_missing_file_is_the_defaults_rather_than_an_error(
    config_home: Path,
) -> None:
    # Every command reads the config, and the overwhelmingly common case
    # is not having written one. That has to be ordinary, not a failure.
    assert load(config_home).timezone is UTC


def test_an_empty_file_is_the_defaults_too(config_home: Path) -> None:
    assert load(_write(config_home, "")).timezone is UTC


def test_a_named_timezone_is_resolved(config_home: Path) -> None:
    config = load(_write(config_home, 'timezone = "Europe/Paris"\n'))

    assert config.timezone == ZoneInfo("Europe/Paris")


def test_local_means_this_machine(config_home: Path) -> None:
    config = load(_write(config_home, 'timezone = "local"\n'))

    # Asserted as an offset rather than as a name: the machine running
    # this may be in any zone, including UTC, and what "local" promises
    # is that a timestamp lands where the machine says it should.
    moment = datetime(2026, 7, 1, 12, tzinfo=UTC)
    assert moment.astimezone(config.timezone).utcoffset() == (
        moment.astimezone().utcoffset()
    )


def test_an_unknown_timezone_says_which_one(config_home: Path) -> None:
    with pytest.raises(ConfigError, match="Mars/Olympus"):
        _ = load(_write(config_home, 'timezone = "Mars/Olympus"\n'))


def test_a_timezone_that_is_not_a_string_is_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match="timezone"):
        _ = load(_write(config_home, "timezone = 2\n"))


def test_a_misspelled_key_is_refused_rather_than_ignored(config_home: Path) -> None:
    # A config file that silently ignores what it does not recognise is
    # the worst kind: the setting looks applied and is not.
    with pytest.raises(ConfigError, match="timezon"):
        _ = load(_write(config_home, 'timezon = "Europe/Paris"\n'))


def test_the_error_names_the_file(config_home: Path) -> None:
    with pytest.raises(ConfigError, match=r"labmon\.toml"):
        _ = load(_write(config_home, "timezone = 2\n"))


def test_broken_toml_is_reported_as_a_config_error(config_home: Path) -> None:
    # Not a traceback: somebody edited a file by hand and needs the line.
    with pytest.raises(ConfigError, match=r"labmon\.toml"):
        _ = load(_write(config_home, 'timezone = "unterminated\n'))


def test_a_file_that_cannot_be_read_is_reported(config_home: Path) -> None:
    # A directory where the file should be. Chosen over an unreadable
    # file because a suite run as root can read one of those anyway.
    config_home.mkdir(parents=True)

    with pytest.raises(ConfigError, match=r"labmon\.toml"):
        _ = load(config_home)


# --------------------------------------------------------------------------
# The monitor section
# --------------------------------------------------------------------------


def test_the_monitor_has_defaults_without_a_section(config_home: Path) -> None:
    monitor = load(config_home).monitor

    assert monitor.refresh == 2.0
    assert monitor.window == "15m"


def test_the_monitor_section_is_read(config_home: Path) -> None:
    config = load(_write(config_home, '[monitor]\nrefresh = "5s"\nwindow = "1h"\n'))

    assert config.monitor.refresh == 5.0
    assert config.monitor.window == "1h"


def test_a_refresh_is_spelled_the_way_since_is(config_home: Path) -> None:
    # One duration spelling across the project. "2" would have to guess
    # a unit, and guessing wrong refreshes sixty times too often.
    with pytest.raises(ConfigError, match="duration"):
        _ = load(_write(config_home, '[monitor]\nrefresh = "2"\n'))


def test_a_refresh_that_is_not_a_string_is_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match="refresh"):
        _ = load(_write(config_home, "[monitor]\nrefresh = 2\n"))


def test_a_refresh_of_zero_is_refused(config_home: Path) -> None:
    # A panel that refreshes every zero seconds is a busy loop against
    # the database, not a fast panel.
    with pytest.raises(ConfigError, match="refresh"):
        _ = load(_write(config_home, '[monitor]\nrefresh = "0s"\n'))


def test_a_window_is_checked_when_it_is_read_not_when_it_is_used(
    config_home: Path,
) -> None:
    # Otherwise the mistake surfaces on the first refresh, after the
    # panel has already drawn itself and cleared the screen.
    with pytest.raises(ConfigError, match="window"):
        _ = load(_write(config_home, '[monitor]\nwindow = "last tuesday"\n'))


def test_an_unknown_monitor_key_is_refused(config_home: Path) -> None:
    # A plausible name for the same idea, which is how the mistake gets
    # made — and silently ignoring it would leave the panel refreshing
    # at the default while the file says otherwise.
    with pytest.raises(ConfigError, match="interval"):
        _ = load(_write(config_home, '[monitor]\ninterval = "2s"\n'))


def test_a_monitor_that_is_not_a_table_is_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match="monitor"):
        _ = load(_write(config_home, 'monitor = "on"\n'))


def test_a_window_that_is_not_a_string_is_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match="window"):
        _ = load(_write(config_home, "[monitor]\nwindow = 15\n"))
