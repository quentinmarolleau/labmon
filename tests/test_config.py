"""The user configuration file: where it lives, and what it accepts."""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from labmon.config import (
    ConfigError,
    Display,
    config_path,
    display_for,
    load,
    load_monitor,
)


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


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------

_PANEL = """
[monitor]
window = "5m"

[[monitor.panels]]
sensor_id = "cryo-77k"
measurement = "temperature"
title = "Cold finger"
precision = 3
warn_above = 80.0
warn_below = 60.0

[[monitor.panels]]
sensor_id = "chamber-1"
"""


def test_no_panels_is_the_ordinary_case(config_home: Path) -> None:
    assert load(config_home).monitor.panels == ()


def test_panels_are_read_in_the_order_they_are_written(config_home: Path) -> None:
    # A layout is a decision about where to look first. Sorting it would
    # discard exactly that.
    panels = load(_write(config_home, _PANEL)).monitor.panels

    assert [panel.sensor_id for panel in panels] == ["cryo-77k", "chamber-1"]


def test_a_panel_carries_what_it_was_given(config_home: Path) -> None:
    first = load(_write(config_home, _PANEL)).monitor.panels[0]

    assert first.measurement == "temperature"
    assert first.title == "Cold finger"
    assert first.precision == 3
    assert first.warn_above == 80.0
    assert first.warn_below == 60.0


def test_a_panel_needs_only_a_sensor(config_home: Path) -> None:
    second = load(_write(config_home, _PANEL)).monitor.panels[1]

    assert second.sensor_id == "chamber-1"
    assert second.measurement is None
    assert second.title is None
    assert second.precision is None


def test_a_panel_without_a_sensor_is_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match="sensor_id"):
        _ = load(_write(config_home, '[[monitor.panels]]\ntitle = "Nowhere"\n'))


def test_an_unknown_panel_key_is_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match="colour"):
        _ = load(
            _write(
                config_home,
                '[[monitor.panels]]\nsensor_id = "a"\ncolour = "red"\n',
            )
        )


def test_a_panel_key_of_the_wrong_type_is_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match="precision"):
        _ = load(
            _write(
                config_home,
                '[[monitor.panels]]\nsensor_id = "a"\nprecision = "three"\n',
            )
        )


def test_a_negative_precision_is_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match="precision"):
        _ = load(
            _write(config_home, '[[monitor.panels]]\nsensor_id = "a"\nprecision = -1\n')
        )


def test_panels_that_are_not_a_list_are_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match="panels"):
        _ = load(_write(config_home, '[monitor]\npanels = "cryo-77k"\n'))


def test_the_panel_names_its_position_when_something_is_wrong(
    config_home: Path,
) -> None:
    # "a panel is missing sensor_id" is unhelpful in a file with nine of
    # them.
    with pytest.raises(ConfigError, match="panel 2"):
        _ = load(
            _write(
                config_home,
                '[[monitor.panels]]\nsensor_id = "a"\n\n'
                + '[[monitor.panels]]\ntitle = "b"\n',
            )
        )


# --------------------------------------------------------------------------
# A layout passed on the command line
# --------------------------------------------------------------------------


def test_a_layout_file_is_read_as_a_monitor_section(tmp_path: Path) -> None:
    # Same shape as `[monitor]`, minus the prefix: a per-procedure
    # layout is the same kind of thing as the one in the user file, and
    # a second schema would be a second thing to document.
    layout = tmp_path / "bakeout.toml"
    _ = layout.write_text(
        'refresh = "1s"\nwindow = "5m"\n\n[[panels]]\nsensor_id = "chamber-1"\n'
    )

    monitor = load_monitor(layout)

    assert monitor.refresh == 1.0
    assert monitor.window == "5m"
    assert [panel.sensor_id for panel in monitor.panels] == ["chamber-1"]


def test_a_layout_file_that_is_not_there_says_which_one(tmp_path: Path) -> None:
    # Passed explicitly, so its absence is a mistake rather than the
    # ordinary case a missing user file is.
    with pytest.raises(ConfigError, match=r"bakeout\.toml"):
        _ = load_monitor(tmp_path / "bakeout.toml")


def test_a_layout_file_is_validated_like_any_other(tmp_path: Path) -> None:
    layout = tmp_path / "bakeout.toml"
    _ = layout.write_text('[[panels]]\ntitle = "no sensor"\n')

    with pytest.raises(ConfigError, match="sensor_id"):
        _ = load_monitor(layout)


def test_a_layout_file_that_is_not_toml_says_so(tmp_path: Path) -> None:
    layout = tmp_path / "bakeout.toml"
    _ = layout.write_text('window = "unterminated\n')

    with pytest.raises(ConfigError, match="not valid TOML"):
        _ = load_monitor(layout)


def test_a_panel_that_is_not_a_table_is_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match="panel 1"):
        _ = load(_write(config_home, '[monitor]\npanels = ["cryo-77k"]\n'))


def test_a_panel_format_it_does_not_know_is_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match="format"):
        _ = load(
            _write(
                config_home,
                '[[monitor.panels]]\nsensor_id = "a"\nformat = "engineering"\n',
            )
        )


def test_a_panel_title_that_is_not_a_string_is_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match="title"):
        _ = load(
            _write(config_home, '[[monitor.panels]]\nsensor_id = "a"\ntitle = 3\n')
        )


def test_a_threshold_that_is_not_a_number_is_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match="warn_above"):
        _ = load(
            _write(
                config_home,
                '[[monitor.panels]]\nsensor_id = "a"\nwarn_above = "hot"\n',
            )
        )


def test_a_whole_number_threshold_is_accepted(config_home: Path) -> None:
    # `warn_above = 80` is how anybody would write it.
    panels = load(
        _write(config_home, '[[monitor.panels]]\nsensor_id = "a"\nwarn_above = 80\n')
    ).monitor.panels

    assert panels[0].warn_above == 80.0


def test_a_boolean_is_never_a_threshold(config_home: Path) -> None:
    # A bool is an int in Python, so this needs saying out loud.
    with pytest.raises(ConfigError, match="warn_below"):
        _ = load(
            _write(
                config_home,
                '[[monitor.panels]]\nsensor_id = "a"\nwarn_below = true\n',
            )
        )


# --------------------------------------------------------------------------
# Per-sensor display rules
# --------------------------------------------------------------------------

_SENSORS = """
[[monitor.sensors]]
sensor_id = "beam-x"
precision = 2

[[monitor.sensors]]
sensor_id = "dual-probe"
measurement = "pressure"
format = "scientific"
"""


def test_no_display_rules_is_the_ordinary_case(config_home: Path) -> None:
    assert load(config_home).monitor.sensors == ()


def test_a_display_rule_carries_what_it_was_given(config_home: Path) -> None:
    rules = load(_write(config_home, _SENSORS)).monitor.sensors

    assert rules[0].sensor_id == "beam-x"
    assert rules[0].precision == 2
    assert rules[0].measurement is None
    assert rules[1].measurement == "pressure"
    assert rules[1].format == "scientific"


def test_a_display_rule_without_a_sensor_is_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match="sensor_id"):
        _ = load(_write(config_home, "[[monitor.sensors]]\nprecision = 2\n"))


def test_a_display_rule_may_not_carry_a_threshold(config_home: Path) -> None:
    # A threshold colours a tile, and a display rule governs sensors
    # that have no tile. Accepting it would invite somebody to write an
    # alarm that never fires.
    with pytest.raises(ConfigError, match="warn_above"):
        _ = load(
            _write(
                config_home,
                '[[monitor.sensors]]\nsensor_id = "a"\nwarn_above = 1.0\n',
            )
        )


def test_a_display_rule_with_a_negative_precision_is_refused(
    config_home: Path,
) -> None:
    with pytest.raises(ConfigError, match="precision"):
        _ = load(
            _write(
                config_home,
                '[[monitor.sensors]]\nsensor_id = "a"\nprecision = -1\n',
            )
        )


def test_a_display_rule_format_it_does_not_know_is_refused(
    config_home: Path,
) -> None:
    with pytest.raises(ConfigError, match="format"):
        _ = load(
            _write(
                config_home,
                '[[monitor.sensors]]\nsensor_id = "a"\nformat = "roman"\n',
            )
        )


def test_display_rules_that_are_not_a_list_are_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match=r"monitor\.sensors"):
        _ = load(_write(config_home, "[monitor]\nsensors = 3\n"))


def test_a_display_rule_that_is_not_a_table_is_refused(config_home: Path) -> None:
    with pytest.raises(ConfigError, match=r"monitor\.sensors 1"):
        _ = load(_write(config_home, "[monitor]\nsensors = [3]\n"))


def test_a_display_rule_names_its_position_when_something_is_wrong(
    config_home: Path,
) -> None:
    with pytest.raises(ConfigError, match=r"monitor\.sensors 2"):
        _ = load(
            _write(
                config_home,
                '[[monitor.sensors]]\nsensor_id = "a"\n'
                + "[[monitor.sensors]]\nprecision = 2\n",
            )
        )


def test_a_rule_naming_the_measurement_wins_over_one_that_does_not() -> None:
    # A probe reporting both a temperature and a pressure can be given
    # different precisions without either rule having to exclude the
    # other.
    loose = Display(sensor_id="probe", precision=1)
    exact = Display(sensor_id="probe", measurement="pressure", precision=4)

    assert display_for([loose, exact], "probe", "pressure") is exact
    assert display_for([loose, exact], "probe", "temperature") is loose


def test_no_rule_for_a_sensor_is_no_rule() -> None:
    assert display_for([Display(sensor_id="a")], "b", "temperature") is None
