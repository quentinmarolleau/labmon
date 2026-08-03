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
import functools
import hashlib
import itertools
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, cast, override

import pint
from asteval import Interpreter

from labmon.gate import RecordingRule

ureg: pint.UnitRegistry = pint.UnitRegistry()

# Defaults describing a common 3.3V, 12-bit ADC. Nothing here is tied to
# a particular board: a 10-bit part running at 5V just passes its own
# values instead.
ADC_RESOLUTION_BITS = 12
ADC_VREF_VOLTS = 3.3

# The name an `expression` conversion uses for the measured voltage.
VOLTAGE_SYMBOL = "v"

# Applied at load time to check a conversion works and to read off the
# unit its results carry.
ONE_VOLT: pint.Quantity = 1.0 * ureg.volt

DEFAULT_MODE = "linear"

# Whether to store the voltage a reading was converted from alongside the
# converted value. On by default: a conversion is not generally
# invertible (piecewise_linear clamps, expression is arbitrary), so
# without the input, correcting a wrong calibration cannot reach readings
# already written.
DEFAULT_STORE_INPUT = True

# Length of the calibration_id tag, in hex characters. Long enough that
# a lab's handful of calibrations will not collide, short enough to read
# in a Grafana legend. Not a security boundary, so truncation is fine.
CALIBRATION_ID_LENGTH = 8

# Significant digits kept when fingerprinting a quantity. Enough to tell
# any two real calibrations apart, few enough that unit conversion noise
# doesn't invent a new id (see _quantity_key).
FINGERPRINT_SIGNIFICANT_DIGITS = 12

# Optional per-channel table saying when a reading is worth recording,
# for a signal that is only meaningful while its instrument is on. See
# labmon.gate.
RECORD_WHEN_KEY = "record_when"

# `for` is a Python keyword, so the parsed field is named `dwell_seconds`;
# the TOML key stays `for` because that is how the setting reads.
DWELL_KEY = "for"

# Which stop threshold pairs with which resume threshold. Naming them
# apart keeps a config from saying `above` and `resume_below`, which
# would be two different gates in one table.
_STOP_KEYS = {"above": True, "below": False}
_RESUME_KEYS = {"above": "resume_above", "below": "resume_below"}

# Free-form documentation about how a calibration was obtained. Read and
# logged at startup, never written to InfluxDB, and excluded from the
# fingerprint so correcting a typo in a note doesn't look like a new
# calibration.
PROVENANCE_KEY = "provenance"

# Shared empty default; safe because it can't be mutated.
NO_PROVENANCE: Mapping[str, object] = MappingProxyType({})

_COMMON_KEYS = ("sensor_id", "measurement")

_OFFSET_UNIT_MESSAGE = (
    "{where} uses an offset unit, which pint refuses to scale."
    + " Use a relative unit instead (e.g. 'delta_degC' rather than 'degC',"
    + " or just 'kelvin')."
)


class CalibrationError(ValueError):
    """Raised when a calibration file is malformed or unusable."""


@dataclass(frozen=True)
class Location:
    """Where in a calibration file a problem was found.

    Carried through parsing so an error names the channel as well as the
    file — "is missing 'sensor_id'" is not much use across five channels.
    Formats itself, so it drops into a message where a path alone would
    otherwise have been interpolated.
    """

    path: Path
    channel: str | None = None

    @override
    def __str__(self) -> str:
        if self.channel is None:
            return str(self.path)
        return f"{self.path}: channel '{self.channel}'"


class Conversion(Protocol):
    """Turns a measured voltage into a physical quantity."""

    def apply(self, voltage: pint.Quantity, /) -> pint.Quantity: ...

    def fingerprint(self) -> str:
        """A canonical description of this conversion, for hashing.

        Built from resolved parameters rather than the file's text, so
        rewriting '42.5 kelvin / volt' as '42.5 kelvin/volt' does not
        look like a new calibration.
        """
        ...


@dataclass(frozen=True)
class LinearConversion:
    """value = voltage * factor."""

    factor: pint.Quantity

    def apply(self, voltage: pint.Quantity, /) -> pint.Quantity:
        return voltage * self.factor

    def fingerprint(self) -> str:
        return f"linear|{_quantity_key(self.factor)}"


@dataclass(frozen=True)
class AffineConversion:
    """value = voltage * factor + offset."""

    factor: pint.Quantity
    offset: pint.Quantity

    def apply(self, voltage: pint.Quantity, /) -> pint.Quantity:
        return voltage * self.factor + self.offset

    def fingerprint(self) -> str:
        return f"affine|{_quantity_key(self.factor)}|{_quantity_key(self.offset)}"


@dataclass(frozen=True)
class InterpolatedConversion:
    """Interpolates between measured points, by spline or straight lines.

    `interpolate` maps a voltage magnitude (in volts) to a value
    magnitude in `unit`; it is prepared once at load time so a reading
    costs a lookup rather than a re-fit.
    """

    interpolate: Callable[[float], float]
    unit: pint.Quantity
    # Kept for the fingerprint: the callable above can't describe itself,
    # and spline and piecewise_linear share this class.
    mode: str
    voltages: tuple[float, ...]
    values: tuple[float, ...]

    def apply(self, voltage: pint.Quantity, /) -> pint.Quantity:
        return self.interpolate(voltage.to(ureg.volt).magnitude) * self.unit

    def fingerprint(self) -> str:
        digits = FINGERPRINT_SIGNIFICANT_DIGITS
        points = ",".join(
            f"{volts:.{digits}g}:{value:.{digits}g}"
            for volts, value in zip(self.voltages, self.values, strict=True)
        )
        return f"{self.mode}|{_quantity_key(self.unit)}|{points}"


@dataclass(frozen=True)
class ExpressionConversion:
    """Evaluates a user-supplied formula in `v`, the voltage in volts."""

    expression: str
    interpreter: Interpreter
    unit: pint.Quantity

    def apply(self, voltage: pint.Quantity, /) -> pint.Quantity:
        return self._evaluate(voltage.to(ureg.volt).magnitude) * self.unit

    def fingerprint(self) -> str:
        # The expression is kept verbatim: whitespace inside a formula is
        # not worth normalising, and a rewritten formula is worth a new id.
        return f"expression|{_quantity_key(self.unit)}|{self.expression}"

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
    store_input: bool = DEFAULT_STORE_INPUT
    provenance: Mapping[str, object] = NO_PROVENANCE
    # None means record everything, which is the default: a gate is a
    # deliberate decision to leave gaps in a series.
    record_when: RecordingRule | None = None

    @functools.cached_property
    def calibration_id(self) -> str:
        """Short hash identifying which conversion produced a reading.

        Written as a tag so a stored reading can be matched back to the
        calibration file that produced it — the file itself is in version
        control, so the database only needs to say *which* revision.

        Cached because it is read once per reading and cannot change: the
        conversion is fixed when the file is parsed. Recomputing it meant
        hashing a fingerprint built from pint unit formatting on every
        sample, which measured at a fifth of the acquisition loop.
        """
        digest = hashlib.sha256(self.conversion.fingerprint().encode()).hexdigest()
        return digest[:CALIBRATION_ID_LENGTH]

    @functools.cached_property
    def unit(self) -> str:
        """The unit tag every reading from this channel carries.

        Derived by converting one volt, so it reflects what the
        conversion actually produces rather than what the file claims.
        Cached for the same reason as calibration_id: a conversion's
        output unit is a property of the calibration, not of a reading,
        and formatting it per sample cost more than building the point.
        """
        return f"{self.conversion.apply(ONE_VOLT).units:~}"


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
        raise CalibrationError(f"{Location(path)}: no [channels.<name>] section found")

    # A top-level store_input sets the default for every channel; each
    # channel may still override it.
    default_store_input = _optional_bool(
        Location(path), document, "store_input", DEFAULT_STORE_INPUT
    )

    channels = cast(dict[str, object], raw_channels)
    return {
        name: _parse_channel(path, name, entry, default_store_input)
        for name, entry in channels.items()
    }


def _parse_channel(
    path: Path, name: str, entry: object, default_store_input: bool
) -> Calibration:
    where = Location(path, name)
    if not isinstance(entry, dict):
        raise CalibrationError(f"{where} must be a [channels.{name}] table")

    fields = cast(dict[str, object], entry)
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
        store_input=_optional_bool(where, fields, "store_input", default_store_input),
        provenance=_optional_provenance(where, fields),
        record_when=_optional_recording_rule(where, fields, conversion),
    )


def _trial_apply(where: Location, conversion: Conversion) -> None:
    """Convert a token voltage so a broken conversion fails at load time."""
    try:
        _ = conversion.apply(ONE_VOLT)
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


def _build_linear(where: Location, fields: dict[str, object]) -> Conversion:
    return LinearConversion(
        factor=_require_quantity(where, fields, "conversion_factor")
    )


def _build_affine(where: Location, fields: dict[str, object]) -> Conversion:
    return AffineConversion(
        factor=_require_quantity(where, fields, "conversion_factor"),
        offset=_require_quantity(where, fields, "offset"),
    )


def _build_spline(where: Location, fields: dict[str, object]) -> Conversion:
    voltages, values = _require_measured_points(where, fields)
    cubic_spline = _import_cubic_spline(where)
    # scipy extrapolates beyond the measured range rather than clamping.
    spline = cubic_spline(voltages, values)

    def interpolate(volts: float) -> float:
        return float(spline(volts))

    return InterpolatedConversion(
        interpolate=interpolate,
        unit=_require_quantity(where, fields, "value_unit"),
        mode="spline",
        voltages=voltages,
        values=values,
    )


def _build_piecewise_linear(where: Location, fields: dict[str, object]) -> Conversion:
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
        interpolate=interpolate,
        unit=_require_quantity(where, fields, "value_unit"),
        mode="piecewise_linear",
        voltages=voltages,
        values=values,
    )


def _build_expression(where: Location, fields: dict[str, object]) -> Conversion:
    expression = _require_str(where, fields, "expression")
    interpreter = Interpreter()
    return ExpressionConversion(
        expression=expression,
        interpreter=interpreter,
        unit=_require_quantity(where, fields, "value_unit"),
    )


_MODE_BUILDERS: dict[str, Callable[[Location, dict[str, object]], Conversion]] = {
    "linear": _build_linear,
    "affine": _build_affine,
    "spline": _build_spline,
    "piecewise_linear": _build_piecewise_linear,
    "expression": _build_expression,
}


def _import_cubic_spline(where: Location) -> Callable[..., Callable[[float], float]]:
    try:
        from scipy.interpolate import CubicSpline
    except ImportError as error:  # pragma: no cover - depends on install extras
        raise CalibrationError(
            f"{where} uses mode 'spline', which needs scipy."
            + " Install it with: uv sync --extra spline"
            + " (or use mode 'piecewise_linear', which needs nothing extra)."
        ) from error
    return cast(Callable[..., Callable[[float], float]], CubicSpline)


def _require_str(where: Location, fields: dict[str, object], key: str) -> str:
    if key not in fields:
        raise CalibrationError(f"{where} is missing '{key}'")
    value = fields[key]
    if not isinstance(value, str):
        raise CalibrationError(f"{where} key '{key}' must be a string")
    return value


def _quantity_key(quantity: pint.Quantity) -> str:
    """Canonical text for a quantity, for use in a fingerprint.

    Reducing to base units means '1e-6 mbar / volt' and '1e-4 Pa / volt'
    fingerprint alike, as they should — they are the same conversion.
    Converting them lands a unit in the last bits apart, though
    (9.999999999999999e-05 against 0.0001), so the magnitude is rounded
    to FINGERPRINT_SIGNIFICANT_DIGITS to absorb that. Well beyond any
    precision a calibration is actually known to, and the alternative is
    an id that changes when nothing did.
    """
    base = quantity.to_base_units()
    magnitude = f"{base.magnitude:.{FINGERPRINT_SIGNIFICANT_DIGITS}g}"
    return f"{magnitude} {base.units}"


def _optional_provenance(
    where: Location, fields: dict[str, object]
) -> Mapping[str, object]:
    if PROVENANCE_KEY not in fields:
        return NO_PROVENANCE
    value = fields[PROVENANCE_KEY]
    if not isinstance(value, dict):
        raise CalibrationError(
            f"{where} key '{PROVENANCE_KEY}' must be a table"
            + f" (a [channels.<name>.{PROVENANCE_KEY}] section)"
        )
    return MappingProxyType(dict(cast(dict[str, object], value)))


def _optional_recording_rule(
    where: Location, fields: dict[str, object], conversion: Conversion
) -> RecordingRule | None:
    """Parse a [channels.<name>.record_when] table, if there is one.

    Everything checkable is checked here rather than on the first reading:
    a threshold in the wrong dimension, a resume threshold on the side
    that would flap, a misspelled key. A gate that silently does nothing
    is worse than no gate, since it looks configured.
    """
    if RECORD_WHEN_KEY not in fields:
        return None

    entry = fields[RECORD_WHEN_KEY]
    if not isinstance(entry, dict):
        raise CalibrationError(
            f"{where} key '{RECORD_WHEN_KEY}' must be a table"
            + f" (a [channels.<name>.{RECORD_WHEN_KEY}] section)"
        )
    gate = cast(dict[str, object], entry)
    gate_where = Location(where.path, f"{where.channel}.{RECORD_WHEN_KEY}")

    stop_keys = [key for key in _STOP_KEYS if key in gate]
    if len(stop_keys) > 1:
        raise CalibrationError(
            f"{gate_where} sets both 'above' and 'below'; a gate has one"
            + " threshold, not both"
        )
    if not stop_keys:
        raise CalibrationError(f"{gate_where} needs 'above' or 'below'")

    stop_key = stop_keys[0]
    resume_key = _RESUME_KEYS[stop_key]
    known = {stop_key, resume_key, DWELL_KEY}
    unknown = sorted(set(gate) - known)
    if unknown:
        wrong_pair = _RESUME_KEYS["below" if stop_key == "above" else "above"]
        if wrong_pair in unknown:
            raise CalibrationError(
                f"{gate_where} sets '{wrong_pair}' alongside '{stop_key}';"
                + f" '{resume_key}' goes with '{stop_key}'"
            )
        raise CalibrationError(
            f"{gate_where} has unknown key {unknown[0]!r};"
            + f" expected one of {', '.join(sorted(known))}"
        )

    value_unit = conversion.apply(ONE_VOLT).units
    threshold = _require_comparable(gate_where, gate, stop_key, value_unit)
    resume = (
        _require_comparable(gate_where, gate, resume_key, value_unit)
        if resume_key in gate
        else threshold
    )
    _check_resume_side(gate_where, stop_key, threshold, resume)

    return RecordingRule(
        threshold=threshold,
        resume_threshold=resume,
        record_above=_STOP_KEYS[stop_key],
        dwell_seconds=_optional_dwell(gate_where, gate),
    )


def _require_comparable(
    where: Location, fields: dict[str, object], key: str, value_unit: pint.Unit
) -> pint.Quantity:
    """Read a threshold, rejecting one the channel's readings can't be compared to."""
    quantity = _require_quantity(where, fields, key)
    try:
        # The result is discarded: this asks pint whether the comparison
        # is meaningful at all, which is the whole point of a dimensioned
        # threshold over a bare number.
        _ = quantity.to(value_unit)
    except pint.DimensionalityError as error:
        raise CalibrationError(
            f"{where} produces {value_unit:~}, so key '{key}' cannot be"
            + f" '{quantity:~}'"
        ) from error
    return quantity


def _check_resume_side(
    where: Location, stop_key: str, threshold: pint.Quantity, resume: pint.Quantity
) -> None:
    """Reject a resume threshold on the wrong side of the stop threshold.

    Between the two the gate would stop and immediately resume — the
    flapping hysteresis exists to prevent.
    """
    # Compared as magnitudes in a common unit rather than as quantities,
    # so "1000 uW" and "1 mW" order correctly.
    resume_magnitude = float(resume.to(threshold.units).magnitude)
    stop_magnitude = float(threshold.magnitude)

    if stop_key == "above" and resume_magnitude < stop_magnitude:
        raise CalibrationError(
            f"{where} resumes at {resume:~} but stops below {threshold:~};"
            + " a resume threshold must be at or above the stop threshold"
        )
    if stop_key == "below" and resume_magnitude > stop_magnitude:
        raise CalibrationError(
            f"{where} resumes at {resume:~} but stops above {threshold:~};"
            + " a resume threshold must be at or below the stop threshold"
        )


def _optional_dwell(where: Location, fields: dict[str, object]) -> float:
    """Read `for` as a duration in seconds, defaulting to no dwell."""
    if DWELL_KEY not in fields:
        return 0.0
    dwell = _require_quantity(where, fields, DWELL_KEY)
    try:
        return float(dwell.to(ureg.second).magnitude)
    except pint.DimensionalityError as error:
        raise CalibrationError(
            f"{where} key '{DWELL_KEY}' must be a duration, not '{dwell:~}'"
        ) from error


def _optional_bool(
    where: Location, fields: dict[str, object], key: str, default: bool
) -> bool:
    if key not in fields:
        return default
    value = fields[key]
    if not isinstance(value, bool):
        raise CalibrationError(f"{where} key '{key}' must be true or false")
    return value


def _require_quantity(
    where: Location, fields: dict[str, object], key: str
) -> pint.Quantity:
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
    where: Location, fields: dict[str, object]
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
    where: Location, fields: dict[str, object], key: str
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
