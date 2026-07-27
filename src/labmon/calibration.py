"""Turn raw ADC counts into dimensioned physical quantities.

An ADC reports a unitless integer count. Converting that to something
physical takes two steps: scaling the count to the voltage the ADC
actually saw (a property of the board), then applying a per-channel
conversion factor (a property of the sensor wired to that channel).

The conversion factor carries its own units, so the physical unit of a
reading is derived rather than declared: multiplying volts by a factor
in `kelvin / volt` yields kelvin, and pint rejects a factor that doesn't
combine sensibly instead of silently producing a wrong number.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pint

ureg: pint.UnitRegistry = pint.UnitRegistry()

# The Arduino Due's SAM3X8E samples 12-bit against a ~3.3V reference.
DUE_RESOLUTION_BITS = 12
DUE_VREF_VOLTS = 3.3

_REQUIRED_KEYS = ("sensor_id", "measurement", "conversion_factor")


class CalibrationError(ValueError):
    """Raised when a calibration file is malformed or unusable."""


@dataclass(frozen=True)
class Calibration:
    """How one ADC channel's voltage maps onto a physical quantity."""

    sensor_id: str
    measurement: str
    conversion_factor: pint.Quantity


def raw_to_voltage(
    raw_count: int,
    resolution_bits: int = DUE_RESOLUTION_BITS,
    v_ref: float = DUE_VREF_VOLTS,
) -> pint.Quantity:
    """Scale a raw ADC count to the voltage it represents.

    Defaults describe the Due; another board just passes its own
    resolution and reference voltage rather than needing a special case.
    """
    # (1 << bits) - 1 is 2**bits - 1, the highest count the ADC reports.
    full_scale = (1 << resolution_bits) - 1
    return (raw_count / full_scale) * v_ref * ureg.volt


def apply_calibration(
    voltage: pint.Quantity, conversion_factor: pint.Quantity
) -> pint.Quantity:
    """Convert a voltage into whatever quantity the factor's units imply."""
    return voltage * conversion_factor


def load_calibration(path: Path) -> dict[str, Calibration]:
    """Read per-channel calibrations from a TOML file, keyed by channel.

    Every factor is parsed and trial-applied here rather than on the
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
    values: dict[str, str] = {}
    for key in _REQUIRED_KEYS:
        if key not in fields:
            raise CalibrationError(f"{path}: channel '{name}' is missing '{key}'")
        value = fields[key]
        if not isinstance(value, str):
            raise CalibrationError(
                f"{path}: channel '{name}' key '{key}' must be a string"
            )
        values[key] = value

    return Calibration(
        sensor_id=values["sensor_id"],
        measurement=values["measurement"],
        conversion_factor=_parse_factor(path, name, values["conversion_factor"]),
    )


def _parse_factor(path: Path, name: str, text: str) -> pint.Quantity:
    where = f"{path}: channel '{name}' conversion_factor {text!r}"
    try:
        factor = ureg(text)
        # Trial-apply so a factor that parses but can't multiply a
        # voltage is caught here rather than on the first reading.
        _ = apply_calibration(1.0 * ureg.volt, factor)
    except pint.OffsetUnitCalculusError as error:
        raise CalibrationError(
            f"{where} uses an offset unit, which pint refuses to multiply."
            + " Use a relative unit instead (e.g. 'delta_degC' rather than"
            + " 'degC', or just 'kelvin')."
        ) from error
    except Exception as error:
        # pint raises assorted types for bad input (UndefinedUnitError,
        # DefinitionSyntaxError, and bare AssertionError/TokenError from
        # the tokenizer), so anything at all becomes a clear config error.
        raise CalibrationError(f"{where} is not a valid quantity: {error}") from error

    return factor
