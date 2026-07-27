from pathlib import Path

import pytest

from labmon.calibration import (
    CalibrationError,
    apply_calibration,
    load_calibration,
    raw_to_voltage,
    ureg,
)


def test_raw_to_voltage_at_zero_count() -> None:
    assert raw_to_voltage(0).magnitude == pytest.approx(0.0)


def test_raw_to_voltage_at_full_scale() -> None:
    reading = raw_to_voltage(4095)

    assert reading.magnitude == pytest.approx(3.3)
    assert reading.units == ureg.volt


def test_raw_to_voltage_at_half_scale() -> None:
    assert raw_to_voltage(2048).magnitude == pytest.approx(3.3 * 2048 / 4095)


def test_raw_to_voltage_honours_custom_resolution_and_reference() -> None:
    # A 10-bit ADC against a 5V reference: full scale is 1023 counts.
    assert raw_to_voltage(1023, resolution_bits=10, v_ref=5.0).magnitude == (
        pytest.approx(5.0)
    )


def test_apply_calibration_derives_the_target_unit() -> None:
    calibrated = apply_calibration(1.5 * ureg.volt, ureg("42.5 kelvin / volt"))

    assert calibrated.magnitude == pytest.approx(63.75)
    assert calibrated.units == ureg.kelvin


def test_apply_calibration_derives_a_pressure_unit() -> None:
    calibrated = apply_calibration(2.0 * ureg.volt, ureg("1e-6 mbar / volt"))

    assert calibrated.magnitude == pytest.approx(2e-6)
    assert calibrated.units == ureg.mbar


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "calibration.toml"
    _ = path.write_text(body, encoding="utf-8")
    return path


def test_load_calibration_reads_every_channel(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "cryo-77k"
        measurement = "temperature"
        conversion_factor = "42.5 kelvin / volt"

        [channels.A1]
        sensor_id = "chamber-1"
        measurement = "pressure"
        conversion_factor = "1e-6 mbar / volt"
        """,
    )

    channels = load_calibration(path)

    assert sorted(channels) == ["A0", "A1"]
    assert channels["A0"].sensor_id == "cryo-77k"
    assert channels["A0"].measurement == "temperature"
    assert channels["A1"].conversion_factor == ureg("1e-6 mbar / volt")


def test_load_calibration_rejects_a_file_without_channels(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "title = 'not a calibration file'\n")

    with pytest.raises(CalibrationError, match="no \\[channels\\.<name>\\] section"):
        _ = load_calibration(path)


def test_load_calibration_rejects_a_channel_that_is_not_a_table(
    tmp_path: Path,
) -> None:
    path = _write_config(tmp_path, "[channels]\nA0 = '42.5 kelvin / volt'\n")

    with pytest.raises(CalibrationError, match=r"A0.*\[channels\.A0\] table"):
        _ = load_calibration(path)


def test_load_calibration_rejects_a_missing_key(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "cryo-77k"
        conversion_factor = "42.5 kelvin / volt"
        """,
    )

    with pytest.raises(CalibrationError, match="A0.*missing.*measurement"):
        _ = load_calibration(path)


def test_load_calibration_rejects_a_non_string_value(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "cryo-77k"
        measurement = "temperature"
        conversion_factor = 42.5
        """,
    )

    with pytest.raises(CalibrationError, match="A0.*conversion_factor.*string"):
        _ = load_calibration(path)


def test_load_calibration_rejects_an_unknown_unit(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "cryo-77k"
        measurement = "temperature"
        conversion_factor = "42.5 kelvni / volt"
        """,
    )

    with pytest.raises(CalibrationError, match="A0.*conversion_factor"):
        _ = load_calibration(path)


def test_load_calibration_rejects_an_offset_unit(tmp_path: Path) -> None:
    # degC is an offset unit: pint refuses to multiply by it, so a factor
    # using it would only blow up mid-run without this check.
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "room-1"
        measurement = "temperature"
        conversion_factor = "10 degC / volt"
        """,
    )

    with pytest.raises(CalibrationError, match="delta_degC"):
        _ = load_calibration(path)


def test_load_calibration_accepts_a_delta_offset_unit(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "room-1"
        measurement = "temperature"
        conversion_factor = "10 delta_degC / volt"
        """,
    )

    channels = load_calibration(path)

    assert channels["A0"].conversion_factor == ureg("10 delta_degC / volt")
