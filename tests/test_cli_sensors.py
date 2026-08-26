"""The sensor subcommands, and the deprecated aliases that reach them.

These used to live beside each sensor module, testing its argparse. The
command line is Typer's now, so they belong here and run through the
real app rather than through a parser built for the test.
"""

from pathlib import Path

import pytest

from labmon.cli import deprecated
from labmon.cli.main import build_app
from labmon.sensors import mock_sensor, serial_sensor
from tests.cli_runner import Invocation, invoke


def _run(*args: str) -> Invocation:
    """Invoke a labmon command, typed so the result's fields resolve."""
    return invoke(build_app(), list(args))


@pytest.fixture
def captured_run(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Record what the command would have run, without running it."""
    calls: list[dict[str, object]] = []

    def fake_run(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(mock_sensor, "run", fake_run)
    return calls


def test_the_remaining_defaults_reach_the_sensor(
    captured_run: list[dict[str, object]],
) -> None:
    # `--measurement` and `--unit` are required, so they are named here
    # rather than defaulted; everything else still has a default.
    result = _run("mock-sensor", "--measurement", "temperature", "--unit", "°C")

    assert result.exit_code == 0
    assert captured_run == [
        {
            "sensor_id": "mock-sensor-1",
            "interval": 5.0,
            "setpoint": 21.0,
            "measurement": "temperature",
            "field": "value",
            "noise": 0.1,
            "log_scale": False,
            "unit": "°C",
            "resolution": None,
            "significant_digits": 6,
            "summary_interval": 30.0,
        }
    ]


def test_every_flag_reaches_the_sensor(
    captured_run: list[dict[str, object]],
) -> None:
    result = _run(
        "mock-sensor",
        "--sensor-id",
        "cryo-77k",
        "--interval",
        "0.5",
        "--setpoint",
        "77",
        "--measurement",
        "temperature",
        "--field",
        "reading",
        "--noise",
        "0.3",
        "--unit",
        "K",
        "--resolution",
        "0.001",
        "--significant-digits",
        "4",
        "--summary-interval",
        "60",
    )

    assert result.exit_code == 0
    assert captured_run[0]["sensor_id"] == "cryo-77k"
    assert captured_run[0]["resolution"] == 0.001
    assert captured_run[0]["significant_digits"] == 4
    assert captured_run[0]["summary_interval"] == 60.0


def test_a_pressure_gauge_walks_in_log_space(
    captured_run: list[dict[str, object]],
) -> None:
    result = _run(
        "mock-sensor",
        "--sensor-id",
        "chamber-1",
        "--measurement",
        "pressure",
        "--setpoint",
        "1e-7",
        "--log-scale",
        "--unit",
        "mbar",
    )

    assert result.exit_code == 0
    assert captured_run[0]["log_scale"] is True
    assert captured_run[0]["setpoint"] == 1e-7


def test_a_summary_interval_of_zero_turns_the_heartbeat_off(
    captured_run: list[dict[str, object]],
) -> None:
    # Typer cannot hand back None from a float option, so zero is the off
    # switch — "summarise every zero seconds" has no other meaning.
    result = _run(
        "mock-sensor",
        "--measurement",
        "temperature",
        "--unit",
        "K",
        "--summary-interval",
        "0",
    )

    assert result.exit_code == 0
    assert captured_run[0]["summary_interval"] is None


def test_an_unknown_log_level_is_rejected(
    captured_run: list[dict[str, object]],
) -> None:
    # Resolving an unknown level with a default would silently turn
    # `--log-level DEGUB` into INFO, and the missing DEBUG lines would
    # then read as a code problem rather than the typo they are.
    result = _run("mock-sensor", "--log-level", "DEGUB")

    assert result.exit_code != 0
    assert captured_run == []


@pytest.mark.usefixtures("captured_run")
def test_a_lower_case_log_level_is_accepted() -> None:
    result = _run(
        "mock-sensor",
        "--measurement",
        "temperature",
        "--unit",
        "K",
        "--log-level",
        "debug",
    )

    assert result.exit_code == 0


@pytest.mark.usefixtures("captured_run")
def test_the_mock_sensor_requires_a_measurement() -> None:
    # A bare run used to write a 21.0 into `temperature` with no unit at
    # all, which is a number nobody can act on: 21 °C is a warm room and
    # 21 K is a cryostat stage, and the reading said neither.
    result = _run("mock-sensor")

    assert result.exit_code != 0
    assert "--measurement" in result.output


@pytest.mark.usefixtures("captured_run")
def test_the_mock_sensor_requires_a_unit() -> None:
    result = _run("mock-sensor", "--measurement", "temperature")

    assert result.exit_code != 0
    assert "--unit" in result.output


@pytest.mark.usefixtures("captured_run")
def test_naming_both_is_enough_to_run() -> None:
    result = _run("mock-sensor", "--measurement", "temperature", "--unit", "K")

    assert result.exit_code == 0


def test_a_measurement_cannot_be_left_empty(
    captured_run: list[dict[str, object]],
) -> None:
    # Requiring the option and then accepting "" for it would be the same
    # unlabelled reading with an extra step.
    result = _run("mock-sensor", "--measurement", "", "--unit", "K")

    assert result.exit_code != 0
    assert captured_run == []


def test_a_unit_cannot_be_left_empty(
    captured_run: list[dict[str, object]],
) -> None:
    result = _run("mock-sensor", "--measurement", "temperature", "--unit", "")

    assert result.exit_code != 0
    assert captured_run == []


def test_the_serial_sensor_requires_a_port_and_a_calibration() -> None:
    result = _run("serial-sensor")

    assert result.exit_code != 0
    assert "--port" in result.output


def test_a_missing_calibration_file_is_refused_before_the_port_opens() -> None:
    # Opening the port first would mean touching the hardware only to
    # discover the config was wrong.
    result = _run("serial-sensor", "--port", "/dev/null", "--calibration", "nope.toml")

    assert result.exit_code != 0
    assert "nope.toml" in result.output


def test_the_serial_sensor_passes_its_settings_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calibration = tmp_path / "cal.toml"
    _ = calibration.write_text(
        '[channels.A0]\nsensor_id = "a"\nmeasurement = "temperature"\n'
        + 'conversion_factor = "1 kelvin / volt"\n',
        encoding="utf-8",
    )
    captured: list[dict[str, object]] = []

    def fake_open(port: str, baudrate: int) -> tuple[str, int]:
        return (port, baudrate)

    def fake_source(handle: object) -> object:
        return handle

    def fake_run(**kwargs: object) -> None:
        captured.append(kwargs)

    monkeypatch.setattr("labmon.sensors.serial_source.open_serial_port", fake_open)
    monkeypatch.setattr("labmon.sensors.serial_source.SerialRawSource", fake_source)
    monkeypatch.setattr(serial_sensor, "run", fake_run)

    result = _run(
        "serial-sensor",
        "--port",
        "/dev/ttyACM0",
        "--calibration",
        str(calibration),
        "--baudrate",
        "9600",
        "--resolution-bits",
        "10",
        "--vref",
        "5.0",
    )

    assert result.exit_code == 0, result.output
    assert captured[0]["resolution_bits"] == 10
    assert captured[0]["v_ref"] == 5.0


def test_the_mock_sensor_alias_still_runs_the_command(
    captured_run: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "mock-sensor",
            "--sensor-id",
            "room-1",
            "--measurement",
            "temperature",
            "--unit",
            "°C",
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        deprecated.mock_sensor_main()

    assert exit_info.value.code == 0
    assert captured_run[0]["sensor_id"] == "room-1"


@pytest.mark.usefixtures("captured_run")
def test_the_alias_names_its_replacement(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Read from stderr rather than caplog: the notice is emitted after
    # logs.configure(), which passes force=True and therefore removes the
    # handler caplog installs. stderr is also where a user sees it.
    monkeypatch.setattr("sys.argv", ["mock-sensor"])

    with pytest.raises(SystemExit):
        deprecated.mock_sensor_main()

    written = capsys.readouterr().err
    assert "deprecated" in written
    assert "labmon mock-sensor" in written
    assert "1.0" in written


def test_the_serial_alias_names_its_replacement(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["serial-sensor", "--help"])

    with pytest.raises(SystemExit):
        deprecated.serial_sensor_main()

    written = capsys.readouterr().err
    assert "deprecated" in written
    assert "labmon serial-sensor" in written


@pytest.mark.usefixtures("captured_run")
def test_the_deprecation_notice_goes_to_stderr_so_a_pipe_stays_clean(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.argv", ["mock-sensor"])

    with pytest.raises(SystemExit):
        deprecated.mock_sensor_main()

    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "deprecated" not in captured.out
