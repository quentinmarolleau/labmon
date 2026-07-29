from pathlib import Path

import pint
import pytest

from labmon.calibration import (
    AffineConversion,
    CalibrationError,
    ExpressionConversion,
    InterpolatedConversion,
    LinearConversion,
    Location,
    _trial_apply,  # pyright: ignore[reportPrivateUsage]
    load_calibration,
    raw_to_voltage,
    ureg,
)


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "calibration.toml"
    _ = path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# raw_to_voltage
# --------------------------------------------------------------------------


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


# --------------------------------------------------------------------------
# Location
# --------------------------------------------------------------------------


def test_location_names_the_channel_when_it_has_one() -> None:
    assert str(Location(Path("calibration.toml"), "A0")) == (
        "calibration.toml: channel 'A0'"
    )


def test_location_is_just_the_file_when_it_has_no_channel() -> None:
    # File-level keys (a top-level store_input) have no channel to name.
    assert str(Location(Path("calibration.toml"))) == "calibration.toml"


# --------------------------------------------------------------------------
# Conversion modes
# --------------------------------------------------------------------------


def test_linear_conversion_derives_the_target_unit() -> None:
    conversion = LinearConversion(factor=ureg("42.5 kelvin / volt"))

    calibrated = conversion.apply(1.5 * ureg.volt)

    assert calibrated.magnitude == pytest.approx(63.75)
    assert calibrated.units == ureg.kelvin


def test_linear_conversion_derives_a_pressure_unit() -> None:
    conversion = LinearConversion(factor=ureg("1e-6 mbar / volt"))

    calibrated = conversion.apply(2.0 * ureg.volt)

    assert calibrated.magnitude == pytest.approx(2e-6)
    assert calibrated.units == ureg.mbar


def test_affine_conversion_adds_the_offset() -> None:
    conversion = AffineConversion(
        factor=ureg("100 kelvin / volt"), offset=ureg("273.15 kelvin")
    )

    calibrated = conversion.apply(0.5 * ureg.volt)

    assert calibrated.magnitude == pytest.approx(323.15)
    assert calibrated.units == ureg.kelvin


def test_spline_conversion_passes_through_its_measured_points(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "diode-1"
        measurement = "temperature"
        mode = "spline"
        voltages = [0.0, 1.0, 2.0, 3.0]
        values = [0.0, 1.0, 4.0, 9.0]
        value_unit = "kelvin"
        """,
    )

    conversion = load_calibration(path)["A0"].conversion

    assert isinstance(conversion, InterpolatedConversion)
    for volts, expected in ((0.0, 0.0), (1.0, 1.0), (2.0, 4.0), (3.0, 9.0)):
        assert conversion.apply(volts * ureg.volt).magnitude == pytest.approx(expected)


def test_spline_conversion_smooths_between_points(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "diode-1"
        measurement = "temperature"
        mode = "spline"
        voltages = [0.0, 1.0, 2.0, 3.0]
        values = [0.0, 1.0, 4.0, 9.0]
        value_unit = "kelvin"
        """,
    )

    conversion = load_calibration(path)["A0"].conversion

    # A cubic through y = x**2 reproduces it exactly, unlike straight
    # segments between the same points (which would give 2.5 at v=1.5).
    calibrated = conversion.apply(1.5 * ureg.volt)
    assert calibrated.magnitude == pytest.approx(2.25)
    assert calibrated.units == ureg.kelvin


def test_piecewise_linear_conversion_interpolates_between_points(
    tmp_path: Path,
) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "diode-1"
        measurement = "temperature"
        mode = "piecewise_linear"
        voltages = [0.0, 1.0, 2.0]
        values = [0.0, 10.0, 30.0]
        value_unit = "kelvin"
        """,
    )

    conversion = load_calibration(path)["A0"].conversion

    assert conversion.apply(0.0 * ureg.volt).magnitude == pytest.approx(0.0)
    assert conversion.apply(1.0 * ureg.volt).magnitude == pytest.approx(10.0)
    assert conversion.apply(0.5 * ureg.volt).magnitude == pytest.approx(5.0)
    assert conversion.apply(1.5 * ureg.volt).magnitude == pytest.approx(20.0)


def test_piecewise_linear_conversion_clamps_outside_the_measured_range(
    tmp_path: Path,
) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "diode-1"
        measurement = "temperature"
        mode = "piecewise_linear"
        voltages = [1.0, 2.0]
        values = [10.0, 20.0]
        value_unit = "kelvin"
        """,
    )

    conversion = load_calibration(path)["A0"].conversion

    # Below and above the measured points, hold the end values rather
    # than extrapolating a line off the end of the data.
    assert conversion.apply(0.0 * ureg.volt).magnitude == pytest.approx(10.0)
    assert conversion.apply(5.0 * ureg.volt).magnitude == pytest.approx(20.0)


def test_expression_conversion_evaluates_the_formula(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "gauge-1"
        measurement = "pressure"
        mode = "expression"
        expression = "10**(2*v - 9)"
        value_unit = "mbar"
        """,
    )

    conversion = load_calibration(path)["A0"].conversion

    assert isinstance(conversion, ExpressionConversion)
    calibrated = conversion.apply(2.0 * ureg.volt)
    assert calibrated.magnitude == pytest.approx(1e-5)
    assert calibrated.units == ureg.mbar


def test_expression_conversion_offers_maths_functions(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "diode-1"
        measurement = "temperature"
        mode = "expression"
        expression = "sqrt(v) * 10"
        value_unit = "kelvin"
        """,
    )

    conversion = load_calibration(path)["A0"].conversion

    assert conversion.apply(4.0 * ureg.volt).magnitude == pytest.approx(20.0)


def test_trial_apply_wraps_an_unexpected_failure() -> None:
    # No config can produce this, but the load-time check still reports
    # a conversion that blows up in some way pint doesn't describe.
    class BrokenConversion:
        def apply(self, _voltage: pint.Quantity, /) -> pint.Quantity:
            raise RuntimeError("sensor on fire")

    where = Location(Path("calibration.toml"), "A0")
    with pytest.raises(CalibrationError, match="cannot be applied.*sensor on fire"):
        _trial_apply(where, BrokenConversion())


# --------------------------------------------------------------------------
# Loading and validation
# --------------------------------------------------------------------------


def test_the_shipped_example_file_is_valid() -> None:
    # Guards against the example drifting from what the loader accepts.
    example = Path(__file__).parent.parent / "calibration.example.toml"

    channels = load_calibration(example)

    assert sorted(channels) == ["A0", "A1", "A2", "A3", "A4"]
    assert {type(channel.conversion) for channel in channels.values()} == {
        LinearConversion,
        AffineConversion,
        InterpolatedConversion,
        ExpressionConversion,
    }


def test_load_calibration_defaults_to_linear_mode(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "cryo-77k"
        measurement = "temperature"
        conversion_factor = "42.5 kelvin / volt"
        """,
    )

    calibration = load_calibration(path)["A0"]

    assert calibration.sensor_id == "cryo-77k"
    assert calibration.measurement == "temperature"
    assert isinstance(calibration.conversion, LinearConversion)


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
        mode = "affine"
        conversion_factor = "1e-6 mbar / volt"
        offset = "1e-9 mbar"
        """,
    )

    channels = load_calibration(path)

    assert sorted(channels) == ["A0", "A1"]
    assert isinstance(channels["A1"].conversion, AffineConversion)


def test_load_calibration_stores_the_conversion_input_by_default(
    tmp_path: Path,
) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "cryo-77k"
        measurement = "temperature"
        conversion_factor = "42.5 kelvin / volt"
        """,
    )

    assert load_calibration(path)["A0"].store_input is True


def test_load_calibration_honours_a_per_channel_store_input(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "cryo-77k"
        measurement = "temperature"
        conversion_factor = "42.5 kelvin / volt"
        store_input = false
        """,
    )

    assert load_calibration(path)["A0"].store_input is False


def test_load_calibration_lets_a_channel_override_the_file_default(
    tmp_path: Path,
) -> None:
    path = _write_config(
        tmp_path,
        """
        store_input = false

        [channels.A0]
        sensor_id = "cryo-77k"
        measurement = "temperature"
        conversion_factor = "42.5 kelvin / volt"

        [channels.A1]
        sensor_id = "chamber-1"
        measurement = "pressure"
        conversion_factor = "1e-6 mbar / volt"
        store_input = true
        """,
    )

    channels = load_calibration(path)

    assert channels["A0"].store_input is False
    assert channels["A1"].store_input is True


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            """
            store_input = "yes"

            [channels.A0]
            sensor_id = "cryo-77k"
            measurement = "temperature"
            conversion_factor = "42.5 kelvin / volt"
            """,
            id="file-level",
        ),
        pytest.param(
            """
            [channels.A0]
            sensor_id = "cryo-77k"
            measurement = "temperature"
            conversion_factor = "42.5 kelvin / volt"
            store_input = "yes"
            """,
            id="channel-level",
        ),
    ],
)
def test_load_calibration_rejects_a_non_boolean_store_input(
    tmp_path: Path, body: str
) -> None:
    path = _write_config(tmp_path, body)

    with pytest.raises(CalibrationError, match="must be true or false"):
        _ = load_calibration(path)


def test_load_calibration_rejects_a_file_without_channels(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "title = 'not a calibration file'\n")

    with pytest.raises(CalibrationError, match=r"no \[channels\.<name>\] section"):
        _ = load_calibration(path)


def test_load_calibration_rejects_a_channel_that_is_not_a_table(tmp_path: Path) -> None:
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


def test_load_calibration_rejects_an_unknown_mode(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "cryo-77k"
        measurement = "temperature"
        mode = "telepathy"
        """,
    )

    with pytest.raises(CalibrationError, match="unknown mode 'telepathy'"):
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


def test_load_calibration_rejects_an_offset_value_unit(tmp_path: Path) -> None:
    # "degC" parses fine on its own; it only fails once something is
    # scaled by it, which the trial application at load time catches.
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "room-1"
        measurement = "temperature"
        mode = "piecewise_linear"
        voltages = [0.0, 1.0]
        values = [0.0, 10.0]
        value_unit = "degC"
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

    conversion = load_calibration(path)["A0"].conversion

    assert isinstance(conversion, LinearConversion)


def test_load_calibration_rejects_an_offset_of_the_wrong_dimension(
    tmp_path: Path,
) -> None:
    # Adding millibars to kelvin is meaningless; pint catches it at load.
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "cryo-77k"
        measurement = "temperature"
        mode = "affine"
        conversion_factor = "100 kelvin / volt"
        offset = "5 mbar"
        """,
    )

    with pytest.raises(CalibrationError, match="inconsistent units"):
        _ = load_calibration(path)


def test_load_calibration_rejects_mismatched_point_counts(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "diode-1"
        measurement = "temperature"
        mode = "piecewise_linear"
        voltages = [0.0, 1.0, 2.0]
        values = [0.0, 10.0]
        value_unit = "kelvin"
        """,
    )

    with pytest.raises(CalibrationError, match="3 voltages but 2 values"):
        _ = load_calibration(path)


def test_load_calibration_rejects_too_few_points(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "diode-1"
        measurement = "temperature"
        mode = "piecewise_linear"
        voltages = [1.0]
        values = [10.0]
        value_unit = "kelvin"
        """,
    )

    with pytest.raises(CalibrationError, match="at least two measured points"):
        _ = load_calibration(path)


def test_load_calibration_accepts_a_falling_voltage_response(tmp_path: Path) -> None:
    # An NTC thermistor's divider voltage drops as it warms, so the
    # measured series runs high-to-low. That must work as-is.
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "ntc-1"
        measurement = "temperature"
        mode = "piecewise_linear"
        voltages = [3.0, 2.0, 1.0]
        values = [273.15, 293.15, 313.15]
        value_unit = "kelvin"
        """,
    )

    conversion = load_calibration(path)["A0"].conversion

    # Each measured point still maps to its own value after the internal
    # flip, and interpolation between them stays correct.
    assert conversion.apply(3.0 * ureg.volt).magnitude == pytest.approx(273.15)
    assert conversion.apply(2.0 * ureg.volt).magnitude == pytest.approx(293.15)
    assert conversion.apply(1.0 * ureg.volt).magnitude == pytest.approx(313.15)
    assert conversion.apply(2.5 * ureg.volt).magnitude == pytest.approx(283.15)


def test_load_calibration_accepts_a_falling_spline_response(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "ntc-1"
        measurement = "temperature"
        mode = "spline"
        voltages = [3.0, 2.0, 1.0, 0.5]
        values = [273.15, 293.15, 313.15, 333.15]
        value_unit = "kelvin"
        """,
    )

    conversion = load_calibration(path)["A0"].conversion

    assert conversion.apply(3.0 * ureg.volt).magnitude == pytest.approx(273.15)
    assert conversion.apply(0.5 * ureg.volt).magnitude == pytest.approx(333.15)


def test_load_calibration_rejects_non_monotonic_voltages(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "diode-1"
        measurement = "temperature"
        mode = "piecewise_linear"
        voltages = [0.0, 2.0, 1.0]
        values = [0.0, 10.0, 20.0]
        value_unit = "kelvin"
        """,
    )

    with pytest.raises(CalibrationError, match="strictly monotonic"):
        _ = load_calibration(path)


def test_load_calibration_rejects_points_that_are_not_an_array(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "diode-1"
        measurement = "temperature"
        mode = "piecewise_linear"
        voltages = "0.0, 1.0"
        values = [0.0, 10.0]
        value_unit = "kelvin"
        """,
    )

    with pytest.raises(CalibrationError, match="voltages.*array of numbers"):
        _ = load_calibration(path)


def test_load_calibration_rejects_a_missing_points_array(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "diode-1"
        measurement = "temperature"
        mode = "piecewise_linear"
        values = [0.0, 10.0]
        value_unit = "kelvin"
        """,
    )

    with pytest.raises(CalibrationError, match="missing 'voltages'"):
        _ = load_calibration(path)


def test_load_calibration_rejects_non_numeric_points(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "diode-1"
        measurement = "temperature"
        mode = "piecewise_linear"
        voltages = [0.0, "one"]
        values = [0.0, 10.0]
        value_unit = "kelvin"
        """,
    )

    with pytest.raises(CalibrationError, match="only numbers"):
        _ = load_calibration(path)


def test_load_calibration_rejects_an_expression_using_an_unknown_name(
    tmp_path: Path,
) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "gauge-1"
        measurement = "pressure"
        mode = "expression"
        expression = "2 * voltage"
        value_unit = "mbar"
        """,
    )

    with pytest.raises(CalibrationError, match="NameError"):
        _ = load_calibration(path)


@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("v > 1", id="boolean"),
        pytest.param("[v, v]", id="list"),
        pytest.param("'a' * 2", id="string"),
    ],
)
def test_load_calibration_rejects_an_expression_that_is_not_numeric(
    tmp_path: Path, expression: str
) -> None:
    path = _write_config(
        tmp_path,
        f"""
        [channels.A0]
        sensor_id = "gauge-1"
        measurement = "pressure"
        mode = "expression"
        expression = "{expression}"
        value_unit = "mbar"
        """,
    )

    with pytest.raises(CalibrationError, match="not a number"):
        _ = load_calibration(path)
