import re
from pathlib import Path
from typing import cast

import pint
import pytest
from asteval import Interpreter

from labmon.calibration import (
    _BAND_ORDER,  # pyright: ignore[reportPrivateUsage]
    AffineConversion,
    Calibration,
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


class _CountingConversion:
    """A conversion that reports how often it was asked to do work.

    Both `calibration_id` and `unit` are cached because they are read
    once per reading and cannot change; counting is how a test says that
    without reaching into the cache itself.
    """

    def __init__(self) -> None:
        self.fingerprints: int = 0
        self.applications: int = 0

    def apply(self, voltage: pint.Quantity, /) -> pint.Quantity:
        self.applications += 1
        return voltage * ureg("42.5 kelvin / volt")

    def fingerprint(self) -> str:
        self.fingerprints += 1
        return "counting|42.5 kelvin / volt"


def _write_config(tmp_path: Path, body: str) -> Path:
    # mkdir so callers can pass a subdirectory to write two configs that
    # differ only in content, not in name.
    tmp_path.mkdir(parents=True, exist_ok=True)
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

        def fingerprint(self) -> str:
            return "broken"

    where = Location(Path("calibration.toml"), "A0")
    with pytest.raises(CalibrationError, match=r"cannot be applied.*sensor on fire"):
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


def _linear_config(factor: str, extra: str = "") -> str:
    return f"""
        [channels.A0]
        sensor_id = "cryo-77k"
        measurement = "temperature"
        conversion_factor = "{factor}"
        {extra}
        """


def test_calibration_id_is_stable_across_equivalent_spellings(
    tmp_path: Path,
) -> None:
    # The id describes the conversion, not the file's text: reformatting
    # the factor must not look like a recalibration.
    spaced = load_calibration(
        _write_config(tmp_path / "a", _linear_config("42.5 kelvin / volt"))
    )["A0"]
    tight = load_calibration(
        _write_config(tmp_path / "b", _linear_config("42.5 kelvin/volt"))
    )["A0"]

    assert spaced.calibration_id == tight.calibration_id


def test_calibration_id_ignores_the_unit_a_factor_is_written_in(
    tmp_path: Path,
) -> None:
    # 1e-6 mbar/V and 1e-4 Pa/V are the same conversion.
    in_mbar = load_calibration(
        _write_config(tmp_path / "a", _linear_config("1e-6 mbar / volt"))
    )["A0"]
    in_pascal = load_calibration(
        _write_config(tmp_path / "b", _linear_config("1e-4 Pa / volt"))
    )["A0"]

    assert in_mbar.calibration_id == in_pascal.calibration_id


def test_calibration_id_changes_when_the_conversion_does(tmp_path: Path) -> None:
    before = load_calibration(
        _write_config(tmp_path / "a", _linear_config("42.5 kelvin / volt"))
    )["A0"]
    after = load_calibration(
        _write_config(tmp_path / "b", _linear_config("42.6 kelvin / volt"))
    )["A0"]

    assert before.calibration_id != after.calibration_id


def test_calibration_id_survives_an_edit_to_the_provenance(tmp_path: Path) -> None:
    # Correcting a note is not a recalibration.
    without = load_calibration(
        _write_config(tmp_path / "a", _linear_config("42.5 kelvin / volt"))
    )["A0"]
    with_notes = load_calibration(
        _write_config(
            tmp_path / "b",
            _linear_config(
                "42.5 kelvin / volt",
                extra='\n[channels.A0.provenance]\nnotes = "against a Lakeshore 336"',
            ),
        )
    )["A0"]

    assert without.calibration_id == with_notes.calibration_id


def test_calibration_id_distinguishes_the_interpolation_modes(
    tmp_path: Path,
) -> None:
    # Same measured points, different curve between them.
    points = """
        sensor_id = "cryo-77k"
        measurement = "temperature"
        voltages = [0.0, 1.0, 2.0]
        values = [10.0, 20.0, 45.0]
        value_unit = "kelvin"
        """
    spline = load_calibration(
        _write_config(tmp_path / "a", f'[channels.A0]\nmode = "spline"\n{points}')
    )["A0"]
    piecewise = load_calibration(
        _write_config(
            tmp_path / "b", f'[channels.A0]\nmode = "piecewise_linear"\n{points}'
        )
    )["A0"]

    assert spline.calibration_id != piecewise.calibration_id


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param(
            'mode = "affine"\nconversion_factor = "1 kelvin / volt"\noffset = "2 kelvin"',
            'mode = "affine"\nconversion_factor = "1 kelvin / volt"\noffset = "3 kelvin"',
            id="affine-offset",
        ),
        pytest.param(
            'mode = "expression"\nexpression = "v * 2"\nvalue_unit = "kelvin"',
            'mode = "expression"\nexpression = "v * 3"\nvalue_unit = "kelvin"',
            id="expression-formula",
        ),
    ],
)
def test_calibration_id_covers_every_conversion_parameter(
    tmp_path: Path, first: str, second: str
) -> None:
    header = '[channels.A0]\nsensor_id = "s"\nmeasurement = "m"\n'
    before = load_calibration(_write_config(tmp_path / "a", header + first))["A0"]
    after = load_calibration(_write_config(tmp_path / "b", header + second))["A0"]

    assert before.calibration_id != after.calibration_id


def test_load_calibration_reads_the_provenance_table(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        [channels.A0]
        sensor_id = "cryo-77k"
        measurement = "temperature"
        conversion_factor = "42.5 kelvin / volt"

        [channels.A0.provenance]
        date = "2026-07-28"
        operator = "QM"
        """,
    )

    provenance = load_calibration(path)["A0"].provenance

    assert provenance == {"date": "2026-07-28", "operator": "QM"}


def test_load_calibration_defaults_to_an_empty_provenance(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _linear_config("42.5 kelvin / volt"))

    assert load_calibration(path)["A0"].provenance == {}


def test_load_calibration_rejects_a_non_table_provenance(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        _linear_config("42.5 kelvin / volt", extra='provenance = "calibrated once"'),
    )

    with pytest.raises(CalibrationError, match="must be a table"):
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

    with pytest.raises(CalibrationError, match=r"A0.*missing.*measurement"):
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

    with pytest.raises(CalibrationError, match=r"A0.*conversion_factor.*string"):
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

    with pytest.raises(CalibrationError, match=r"A0.*conversion_factor"):
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

    with pytest.raises(CalibrationError, match=r"voltages.*array of numbers"):
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


def test_calibration_id_is_computed_once_per_calibration() -> None:
    """It is read once per reading, so recomputing it dominated the loop."""
    calibration = Calibration(
        sensor_id="cryo-77k",
        measurement="temperature",
        conversion=_CountingConversion(),
    )
    conversion = cast(_CountingConversion, calibration.conversion)

    first = calibration.calibration_id
    second = calibration.calibration_id

    assert first == second
    assert conversion.fingerprints == 1


def test_unit_is_derived_from_the_conversion_and_computed_once() -> None:
    """The unit tag describes the calibration, not the individual reading."""
    calibration = Calibration(
        sensor_id="cryo-77k",
        measurement="temperature",
        conversion=_CountingConversion(),
    )
    conversion = cast(_CountingConversion, calibration.conversion)

    assert calibration.unit == "K"
    assert calibration.unit == "K"
    assert conversion.applications == 1


def test_unit_matches_what_a_reading_actually_carries() -> None:
    """A cached unit is only safe while it agrees with the live conversion."""
    calibration = Calibration(
        sensor_id="cryo-77k",
        measurement="temperature",
        conversion=LinearConversion(factor=ureg("42.5 kelvin / volt")),
    )

    converted = calibration.conversion.apply(2.0 * ureg.volt)

    assert calibration.unit == f"{converted.units:~}"


# --------------------------------------------------------------------------
# stop_recording_when — the per-channel recording gate
# --------------------------------------------------------------------------


_GATED_CHANNEL = """
[channels.A0]
sensor_id = "laser-1"
measurement = "power"
conversion_factor = "50.0 mW / volt"

[channels.A0.stop_recording_when]
"""


def test_a_channel_without_stop_recording_when_has_no_rule(tmp_path: Path) -> None:
    """The gate is opt-in: an unconfigured channel records everything."""
    channels = load_calibration(
        _write_config(
            tmp_path,
            """
            [channels.A0]
            sensor_id = "laser-1"
            measurement = "power"
            conversion_factor = "50.0 mW / volt"
            """,
        )
    )

    assert channels["A0"].stop_recording_when is None


def test_below_stops_recording_under_it(tmp_path: Path) -> None:
    channels = load_calibration(
        _write_config(tmp_path, _GATED_CHANNEL + 'below = "1.0 mW"\n')
    )
    rule = channels["A0"].stop_recording_when

    assert rule is not None
    assert rule.below == ureg("1.0 mW")
    assert rule.above is None
    # No deadband given, so the stop bound stands in for one.
    assert rule.resume_floor == ureg("1.0 mW")
    assert rule.resume_ceiling is None
    assert rule.dwell_seconds == 0.0
    assert not rule.raw_voltage


def test_above_stops_recording_over_it(tmp_path: Path) -> None:
    channels = load_calibration(
        _write_config(tmp_path, _GATED_CHANNEL + 'above = "9.0 mW"\n')
    )
    rule = channels["A0"].stop_recording_when

    assert rule is not None
    assert rule.below is None
    assert rule.above == ureg("9.0 mW")


def test_both_bounds_make_a_band(tmp_path: Path) -> None:
    """A channel can be meaningless at both ends: dark at one, railed at the other."""
    channels = load_calibration(
        _write_config(tmp_path, _GATED_CHANNEL + 'below = "1 mW"\nabove = "9 mW"\n')
    )
    rule = channels["A0"].stop_recording_when

    assert rule is not None
    assert rule.below == ureg("1 mW")
    assert rule.above == ureg("9 mW")


def test_for_sets_the_dwell(tmp_path: Path) -> None:
    channels = load_calibration(
        _write_config(tmp_path, _GATED_CHANNEL + 'below = "1.0 mW"\nfor = "5 min"\n')
    )
    rule = channels["A0"].stop_recording_when

    assert rule is not None
    assert rule.dwell_seconds == 300.0


def test_resume_above_sets_the_deadband_on_below(tmp_path: Path) -> None:
    channels = load_calibration(
        _write_config(
            tmp_path, _GATED_CHANNEL + 'below = "1.0 mW"\nresume_above = "10.0 mW"\n'
        )
    )
    rule = channels["A0"].stop_recording_when

    assert rule is not None
    assert rule.resume_floor == ureg("10.0 mW")


def test_resume_below_sets_the_deadband_on_above(tmp_path: Path) -> None:
    channels = load_calibration(
        _write_config(
            tmp_path, _GATED_CHANNEL + 'above = "10.0 mW"\nresume_below = "9.0 mW"\n'
        )
    )
    rule = channels["A0"].stop_recording_when

    assert rule is not None
    assert rule.resume_ceiling == ureg("9.0 mW")


def test_raw_voltage_gates_before_the_conversion(tmp_path: Path) -> None:
    """The bounds are then in volts, whatever the channel converts to."""
    channels = load_calibration(
        _write_config(
            tmp_path,
            _GATED_CHANNEL + 'raw_voltage = true\nbelow = "100 mV"\nabove = "3.0 V"\n',
        )
    )
    rule = channels["A0"].stop_recording_when

    assert rule is not None
    assert rule.raw_voltage
    assert rule.below == ureg("100 mV")


def test_raw_voltage_must_be_a_boolean(tmp_path: Path) -> None:
    with pytest.raises(CalibrationError, match="must be true or false"):
        _ = load_calibration(
            _write_config(
                tmp_path, _GATED_CHANNEL + 'raw_voltage = "yes"\nbelow = "1 mW"\n'
            )
        )


def test_a_bound_in_the_wrong_dimension_is_rejected(tmp_path: Path) -> None:
    """The channel produces milliwatts; a bound in kelvin cannot gate it."""
    with pytest.raises(CalibrationError, match="produces mW"):
        _ = load_calibration(
            _write_config(tmp_path, _GATED_CHANNEL + 'below = "1.0 kelvin"\n')
        )


def test_a_raw_voltage_bound_must_be_a_voltage(tmp_path: Path) -> None:
    """The channel's own unit is irrelevant once the gate reads the input."""
    with pytest.raises(CalibrationError, match="gates on the raw voltage"):
        _ = load_calibration(
            _write_config(
                tmp_path, _GATED_CHANNEL + 'raw_voltage = true\nbelow = "1.0 mW"\n'
            )
        )


def test_a_bare_number_is_rejected(tmp_path: Path) -> None:
    """Dimensioned, so it cannot silently mean the wrong unit."""
    with pytest.raises(CalibrationError, match="must be a string"):
        _ = load_calibration(_write_config(tmp_path, _GATED_CHANNEL + "below = 1.0\n"))


def test_stop_recording_when_needs_a_bound(tmp_path: Path) -> None:
    with pytest.raises(CalibrationError, match="needs 'below' or 'above'"):
        _ = load_calibration(_write_config(tmp_path, _GATED_CHANNEL + 'for = "30s"\n'))


def test_a_deadband_without_its_stop_bound_is_rejected(tmp_path: Path) -> None:
    """Alone it deadbands nothing, so it reads as configured while doing nothing."""
    with pytest.raises(CalibrationError, match="'resume_above' without 'below'"):
        _ = load_calibration(
            _write_config(
                tmp_path, _GATED_CHANNEL + 'above = "9 mW"\nresume_above = "1 mW"\n'
            )
        )


def test_a_deadband_inside_its_own_stop_bound_is_rejected(tmp_path: Path) -> None:
    """Below the level it deadbands, the gate would resume the moment it stopped."""
    with pytest.raises(CalibrationError, match=re.escape(_BAND_ORDER)):
        _ = load_calibration(
            _write_config(
                tmp_path,
                _GATED_CHANNEL + 'below = "10.0 mW"\nresume_above = "1.0 mW"\n',
            )
        )


def test_a_deadband_outside_its_own_stop_bound_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CalibrationError, match=re.escape(_BAND_ORDER)):
        _ = load_calibration(
            _write_config(
                tmp_path,
                _GATED_CHANNEL + 'above = "1.0 mW"\nresume_below = "10.0 mW"\n',
            )
        )


def test_stop_bounds_that_leave_no_band_are_rejected(tmp_path: Path) -> None:
    """Nothing is inside a band whose floor is above its ceiling."""
    with pytest.raises(CalibrationError, match=re.escape(_BAND_ORDER)):
        _ = load_calibration(
            _write_config(tmp_path, _GATED_CHANNEL + 'below = "9 mW"\nabove = "1 mW"\n')
        )


def test_a_band_of_zero_width_is_rejected(tmp_path: Path) -> None:
    """Equal bounds are not a degenerate case to allow: nothing is inside them."""
    with pytest.raises(CalibrationError, match=re.escape(_BAND_ORDER)):
        _ = load_calibration(
            _write_config(tmp_path, _GATED_CHANNEL + 'below = "1 mW"\nabove = "1 mW"\n')
        )


def test_deadbands_that_meet_are_rejected(tmp_path: Path) -> None:
    """A resume band with no width leaves nothing to come back into."""
    with pytest.raises(CalibrationError, match=re.escape(_BAND_ORDER)):
        _ = load_calibration(
            _write_config(
                tmp_path,
                _GATED_CHANNEL
                + 'below = "1 mW"\nabove = "9 mW"\n'
                + 'resume_above = "5 mW"\nresume_below = "5 mW"\n',
            )
        )


def test_a_deadband_equal_to_its_stop_bound_is_accepted(tmp_path: Path) -> None:
    """Which is exactly what an omitted deadband means, written out."""
    channels = load_calibration(
        _write_config(
            tmp_path, _GATED_CHANNEL + 'below = "1 mW"\nresume_above = "1 mW"\n'
        )
    )
    rule = channels["A0"].stop_recording_when

    assert rule is not None
    assert rule.resume_floor == ureg("1 mW")


def test_deadbands_that_cross_each_other_are_rejected(tmp_path: Path) -> None:
    """Individually inside their stop bounds, but with no room left to resume in."""
    with pytest.raises(CalibrationError, match=re.escape(_BAND_ORDER)):
        _ = load_calibration(
            _write_config(
                tmp_path,
                _GATED_CHANNEL
                + 'below = "1 mW"\nabove = "9 mW"\n'
                + 'resume_above = "8 mW"\nresume_below = "2 mW"\n',
            )
        )


def test_a_full_ordering_is_accepted(tmp_path: Path) -> None:
    """below <= resume_above < resume_below <= above, all four written out."""
    channels = load_calibration(
        _write_config(
            tmp_path,
            _GATED_CHANNEL
            + 'below = "1 mW"\nresume_above = "2 mW"\n'
            + 'resume_below = "8 mW"\nabove = "9 mW"\n',
        )
    )
    rule = channels["A0"].stop_recording_when

    assert rule is not None
    assert (rule.resume_floor, rule.resume_ceiling) == (ureg("2 mW"), ureg("8 mW"))


def test_bounds_are_ordered_across_units(tmp_path: Path) -> None:
    """'1000 uW' and '1 mW' are the same level however they are written."""
    with pytest.raises(CalibrationError, match=re.escape(_BAND_ORDER)):
        _ = load_calibration(
            _write_config(
                tmp_path,
                _GATED_CHANNEL + 'below = "1 mW"\nresume_above = "900 uW"\n',
            )
        )


def test_a_dwell_that_is_not_a_time_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CalibrationError, match="a duration"):
        _ = load_calibration(
            _write_config(tmp_path, _GATED_CHANNEL + 'below = "1 mW"\nfor = "30 mW"\n')
        )


def test_a_dwell_written_as_1m_says_that_m_is_metres(tmp_path: Path) -> None:
    """The obvious way to write one minute, and it parses as a length."""
    with pytest.raises(CalibrationError, match="minutes are 'min'"):
        _ = load_calibration(
            _write_config(tmp_path, _GATED_CHANNEL + 'below = "1 mW"\nfor = "1m"\n')
        )


def test_a_dwell_in_minutes_is_converted_to_seconds(tmp_path: Path) -> None:
    channels = load_calibration(
        _write_config(tmp_path, _GATED_CHANNEL + 'below = "1 mW"\nfor = "1 min"\n')
    )
    rule = channels["A0"].stop_recording_when

    assert rule is not None
    assert rule.dwell_seconds == 60.0


def test_stop_recording_when_must_be_a_table(tmp_path: Path) -> None:
    with pytest.raises(CalibrationError, match="must be a table"):
        _ = load_calibration(
            _write_config(
                tmp_path,
                """
                [channels.A0]
                sensor_id = "laser-1"
                measurement = "power"
                conversion_factor = "50.0 mW / volt"
                stop_recording_when = "below 1 mW"
                """,
            )
        )


def test_an_unknown_key_in_stop_recording_when_is_rejected(tmp_path: Path) -> None:
    """A typo'd 'resume' would otherwise silently mean no deadband at all."""
    with pytest.raises(CalibrationError, match="unknown key"):
        _ = load_calibration(
            _write_config(
                tmp_path, _GATED_CHANNEL + 'below = "1 mW"\nresume = "9 mW"\n'
            )
        )


def test_the_gate_is_not_part_of_the_calibration_id(tmp_path: Path) -> None:
    """Changing when to record does not change how a reading was converted."""
    ungated = load_calibration(
        _write_config(
            tmp_path / "plain",
            """
            [channels.A0]
            sensor_id = "laser-1"
            measurement = "power"
            conversion_factor = "50.0 mW / volt"
            """,
        )
    )
    gated = load_calibration(
        _write_config(tmp_path / "gated", _GATED_CHANNEL + 'below = "1.0 mW"\n')
    )

    assert gated["A0"].calibration_id == ungated["A0"].calibration_id


# A conversion may be undefined at a point without being broken
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expression",
    [
        "5.0 / v",  # pole at zero
        "5.0 / (1.0 - v)",  # pole at the first probe voltage
        "5.0 / (0.5 - v)",  # pole at the second
        "5.0 / (2.0 - v)",  # pole at the third
        "log(v)",  # undefined at zero
    ],
)
def test_a_conversion_with_a_pole_still_loads(tmp_path: Path, expression: str) -> None:
    """No single probe voltage avoids every pole; moving one relocates it."""
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

    calibration = load_calibration(path)["A0"]

    assert calibration.unit == "mbar"


@pytest.mark.parametrize(
    ("expression", "message"),
    [("1.0 / 0.0", "ZeroDivisionError"), ("nosuchfunc(v)", "NameError")],
)
def test_a_conversion_broken_everywhere_is_still_rejected(
    tmp_path: Path, expression: str, message: str
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

    with pytest.raises(CalibrationError, match=message):
        _ = load_calibration(path)


def test_the_unit_of_a_conversion_with_a_pole_is_still_derivable() -> None:
    """`unit` probes the same way, so a pole must not break it either."""
    conversion = ExpressionConversion(
        expression="5.0 / (1.0 - v)",
        interpreter=Interpreter(),
        unit=1.0 * ureg.mbar,
    )
    calibration = Calibration(
        sensor_id="gauge-1", measurement="pressure", conversion=conversion
    )

    assert calibration.unit == "mbar"
