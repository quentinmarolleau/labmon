"""Turn raw ADC counts into dimensioned physical quantities.

An ADC reports a unitless integer count. Converting that to something
physical takes two steps: scaling the count to the voltage the ADC
actually saw (a property of the board), then applying a per-channel
conversion (a property of the sensor wired to that channel).

Five conversion modes cover the usual ways a sensor's response gets
characterised:

- `linear` — one dimensioned factor, e.g. from a fit through the origin.
- `affine` — a factor plus an offset, for a fit that doesn't pass
  through the origin.
- `spline` — a cubic spline through measured (voltage, value) points,
  for a curved response. Needs scipy (`uv sync --extra spline`).
- `piecewise_linear` — straight segments between the same measured
  points, when a spline is more than the data justifies.
- `expression` — an arbitrary formula in `v`, for a response with a
  known closed form.

Wherever a unit can be derived it is, rather than declared: multiplying
volts by a factor in `kelvin / volt` yields kelvin, and pint rejects a
conversion that doesn't combine sensibly instead of silently producing
a wrong number. The interpolation and expression modes can't derive
one, so they state their `value_unit` explicitly.

Voltages are always in volts — the ADC's own output. A datasheet quoted
in millivolts gets scaled once, visibly, when writing the config.
"""

import bisect
import itertools
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import pint
from asteval import Interpreter

ureg: pint.UnitRegistry = pint.UnitRegistry()

# Defaults describing a common 3.3V, 12-bit ADC. Nothing here is tied to
# a particular board: a 10-bit part running at 5V just passes its own
# values instead.
ADC_RESOLUTION_BITS = 12
ADC_VREF_VOLTS = 3.3

# The name an `expression` conversion uses for the measured voltage.
VOLTAGE_SYMBOL = "v"

DEFAULT_MODE = "linear"

_COMMON_KEYS = ("sensor_id", "measurement")

_OFFSET_UNIT_MESSAGE = (
    "{where} uses an offset unit, which pint refuses to scale."
    + " Use a relative unit instead (e.g. 'delta_degC' rather than 'degC',"
    + " or just 'kelvin')."
)


class CalibrationError(ValueError):
    """Raised when a calibration file is malformed or unusable."""


class Conversion(Protocol):
    """Turns a measured voltage into a physical quantity."""

    def apply(self, voltage: pint.Quantity, /) -> pint.Quantity: ...


@dataclass(frozen=True)
class LinearConversion:
    """value = voltage * factor."""

    factor: pint.Quantity

    def apply(self, voltage: pint.Quantity, /) -> pint.Quantity:
        return voltage * self.factor


@dataclass(frozen=True)
class AffineConversion:
    """value = voltage * factor + offset."""

    factor: pint.Quantity
    offset: pint.Quantity

    def apply(self, voltage: pint.Quantity, /) -> pint.Quantity:
        return voltage * self.factor + self.offset


@dataclass(frozen=True)
class InterpolatedConversion:
    """Interpolates between measured points, by spline or straight lines.

    `interpolate` maps a voltage magnitude (in volts) to a value
    magnitude in `unit`; it is prepared once at load time so a reading
    costs a lookup rather than a re-fit.
    """

    interpolate: Callable[[float], float]
    unit: pint.Quantity

    def apply(self, voltage: pint.Quantity, /) -> pint.Quantity:
        return self.interpolate(voltage.to(ureg.volt).magnitude) * self.unit


@dataclass(frozen=True)
class ExpressionConversion:
    """Evaluates a user-supplied formula in `v`, the voltage in volts."""

    expression: str
    interpreter: Interpreter
    unit: pint.Quantity

    def apply(self, voltage: pint.Quantity, /) -> pint.Quantity:
        return self._evaluate(voltage.to(ureg.volt).magnitude) * self.unit

    def _evaluate(self, volts: float) -> float:
        self.interpreter.symtable[VOLTAGE_SYMBOL] = volts
        # asteval reports problems on its error list rather than raising,
        # so a silent None would otherwise reach the field value.
        self.interpreter.error = []
        result = self.interpreter(self.expression)
        if self.interpreter.error:
            raise CalibrationError(
                f"expression {self.expression!r} failed at {VOLTAGE_SYMBOL}={volts}:"
                + f" {self.interpreter.error[0].get_error()[0]}"
            )
        if not isinstance(result, (int, float)) or isinstance(result, bool):
            raise CalibrationError(
                f"expression {self.expression!r} returned {result!r},"
                + " which is not a number"
            )
        return float(result)


@dataclass(frozen=True)
class Calibration:
    """How one ADC channel's voltage maps onto a physical quantity."""

    sensor_id: str
    measurement: str
    conversion: Conversion


def raw_to_voltage(
    raw_count: float,
    resolution_bits: int = ADC_RESOLUTION_BITS,
    v_ref: float = ADC_VREF_VOLTS,
) -> pint.Quantity:
    """Scale a raw ADC count to the voltage it represents.

    Defaults suit a 3.3V, 12-bit ADC; another part just passes its own
    resolution and reference voltage rather than needing a special case.

    The count may be fractional, since a board that averages several
    conversions per reading resolves below one ADC step.
    """
    # (1 << bits) - 1 is 2**bits - 1, the highest count the ADC reports.
    full_scale = (1 << resolution_bits) - 1
    return (raw_count / full_scale) * v_ref * ureg.volt


def load_calibration(path: Path) -> dict[str, Calibration]:
    """Read per-channel calibrations from a TOML file, keyed by channel.

    Every conversion is built and trial-applied here rather than on the
    first reading, so a bad config fails at startup instead of part-way
    through a run.
    """
    with path.open("rb") as handle:
        document: dict[str, object] = tomllib.load(handle)

    raw_channels: object = document.get("channels")
    if not isinstance(raw_channels, dict) or not raw_channels:
        raise CalibrationError(f"{path}: no [channels.<name>] section found")

    channels = cast(dict[str, object], raw_channels)
    return {
        name: _parse_channel(path, name, entry) for name, entry in channels.items()
    }


def _parse_channel(path: Path, name: str, entry: object) -> Calibration:
    if not isinstance(entry, dict):
        raise CalibrationError(
            f"{path}: channel '{name}' must be a [channels.{name}] table"
        )

    fields = cast(dict[str, object], entry)
    where = f"{path}: channel '{name}'"
    common = {key: _require_str(where, fields, key) for key in _COMMON_KEYS}
    mode = _require_str(where, fields, "mode") if "mode" in fields else DEFAULT_MODE

    builder = _MODE_BUILDERS.get(mode)
    if builder is None:
        raise CalibrationError(
            f"{where} has unknown mode {mode!r};"
            + f" expected one of {', '.join(sorted(_MODE_BUILDERS))}"
        )

    conversion = builder(where, fields)
    _trial_apply(where, conversion)
    return Calibration(
        sensor_id=common["sensor_id"],
        measurement=common["measurement"],
        conversion=conversion,
    )


def _trial_apply(where: str, conversion: Conversion) -> None:
    """Convert a token voltage so a broken conversion fails at load time."""
    try:
        _ = conversion.apply(1.0 * ureg.volt)
    except CalibrationError:
        raise
    except pint.OffsetUnitCalculusError as error:
        raise CalibrationError(_OFFSET_UNIT_MESSAGE.format(where=where)) from error
    except pint.DimensionalityError as error:
        raise CalibrationError(f"{where} has inconsistent units: {error}") from error
    except Exception as error:
        raise CalibrationError(
            f"{where} cannot be applied to a voltage: {error}"
        ) from error


def _build_linear(where: str, fields: dict[str, object]) -> Conversion:
    return LinearConversion(factor=_require_quantity(where, fields, "conversion_factor"))


def _build_affine(where: str, fields: dict[str, object]) -> Conversion:
    return AffineConversion(
        factor=_require_quantity(where, fields, "conversion_factor"),
        offset=_require_quantity(where, fields, "offset"),
    )


def _build_spline(where: str, fields: dict[str, object]) -> Conversion:
    voltages, values = _require_measured_points(where, fields)
    cubic_spline = _import_cubic_spline(where)
    # scipy extrapolates beyond the measured range rather than clamping.
    spline = cubic_spline(voltages, values)

    def interpolate(volts: float) -> float:
        return float(spline(volts))

    return InterpolatedConversion(
        interpolate=interpolate, unit=_require_quantity(where, fields, "value_unit")
    )


def _build_piecewise_linear(where: str, fields: dict[str, object]) -> Conversion:
    voltages, values = _require_measured_points(where, fields)

    def interpolate(volts: float) -> float:
        # Clamp outside the measured range rather than extrapolating a
        # straight line off the end of the data.
        if volts <= voltages[0]:
            return values[0]
        if volts >= voltages[-1]:
            return values[-1]
        index = bisect.bisect_right(voltages, volts)
        v_low, v_high = voltages[index - 1], voltages[index]
        value_low, value_high = values[index - 1], values[index]
        span = (volts - v_low) / (v_high - v_low)
        return value_low + (value_high - value_low) * span

    return InterpolatedConversion(
        interpolate=interpolate, unit=_require_quantity(where, fields, "value_unit")
    )


def _build_expression(where: str, fields: dict[str, object]) -> Conversion:
    expression = _require_str(where, fields, "expression")
    interpreter = Interpreter()
    return ExpressionConversion(
        expression=expression,
        interpreter=interpreter,
        unit=_require_quantity(where, fields, "value_unit"),
    )


_MODE_BUILDERS: dict[str, Callable[[str, dict[str, object]], Conversion]] = {
    "linear": _build_linear,
    "affine": _build_affine,
    "spline": _build_spline,
    "piecewise_linear": _build_piecewise_linear,
    "expression": _build_expression,
}


def _import_cubic_spline(where: str) -> Callable[..., Callable[[float], float]]:
    try:
        from scipy.interpolate import CubicSpline
    except ImportError as error:  # pragma: no cover - depends on install extras
        raise CalibrationError(
            f"{where} uses mode 'spline', which needs scipy."
            + " Install it with: uv sync --extra spline"
            + " (or use mode 'piecewise_linear', which needs nothing extra)."
        ) from error
    return cast(Callable[..., Callable[[float], float]], CubicSpline)


def _require_str(where: str, fields: dict[str, object], key: str) -> str:
    if key not in fields:
        raise CalibrationError(f"{where} is missing '{key}'")
    value = fields[key]
    if not isinstance(value, str):
        raise CalibrationError(f"{where} key '{key}' must be a string")
    return value


def _require_quantity(where: str, fields: dict[str, object], key: str) -> pint.Quantity:
    text = _require_str(where, fields, key)
    try:
        return ureg(text)
    except pint.OffsetUnitCalculusError as error:
        # e.g. "10 degC / volt" — pint refuses this while parsing, since
        # scaling an offset unit is ambiguous.
        raise CalibrationError(
            _OFFSET_UNIT_MESSAGE.format(where=f"{where} key '{key}'")
        ) from error
    except Exception as error:
        # pint raises assorted types for bad input (UndefinedUnitError,
        # DefinitionSyntaxError, and bare AssertionError/TokenError from
        # the tokenizer), so anything at all becomes a clear config error.
        raise CalibrationError(
            f"{where} key '{key}' is not a valid quantity: {error}"
        ) from error


def _require_measured_points(
    where: str, fields: dict[str, object]
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    voltages = _require_floats(where, fields, "voltages")
    values = _require_floats(where, fields, "values")

    if len(voltages) != len(values):
        raise CalibrationError(
            f"{where} has {len(voltages)} voltages but {len(values)} values;"
            + " they must pair up"
        )
    if len(voltages) < 2:
        raise CalibrationError(f"{where} needs at least two measured points")

    pairs = list(itertools.pairwise(voltages))
    if all(later > earlier for earlier, later in pairs):
        return voltages, values
    if all(later < earlier for earlier, later in pairs):
        # A falling response is perfectly normal (an NTC thermistor's
        # divider voltage drops as it warms). Both interpolators need
        # ascending voltages, so flip the series rather than making the
        # user rewrite their measurements backwards.
        return tuple(reversed(voltages)), tuple(reversed(values))
    raise CalibrationError(
        f"{where} voltages must be strictly monotonic"
        + " (either increasing or decreasing throughout)"
    )


def _require_floats(
    where: str, fields: dict[str, object], key: str
) -> tuple[float, ...]:
    if key not in fields:
        raise CalibrationError(f"{where} is missing '{key}'")
    value = fields[key]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise CalibrationError(f"{where} key '{key}' must be an array of numbers")

    parsed: list[float] = []
    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            raise CalibrationError(
                f"{where} key '{key}' must contain only numbers, got {item!r}"
            )
        parsed.append(float(item))
    return tuple(parsed)
