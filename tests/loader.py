"""Import the files that ship to be executed, with a declared surface.

`demo/`, `scripts/` and `templates/` hold code that is measured like any
other and that no `import` statement can reach: nothing there is inside a
package, and one directory has a hyphen in its name. Both halves of that
are a path problem, so both are solved with a path.

Loading by path costs the type checker everything it knows, and an
untyped module leaks `Any` through every assertion that touches it. The
protocols below are the fix, and they earn their length twice: the tests
stay fully checked, and each one is a written-down statement of the
surface a shipped script is expected to have, so renaming a function out
from under its tests fails the type check rather than the run.

Not named `test_*`, so pytest does not collect it.
"""

import importlib.util
import logging
import runpy
import ssl
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

_ROOT = Path(__file__).resolve().parent.parent


def path_to(relative: str) -> Path:
    """One shipped script, by its path from the repository root."""
    return _ROOT / relative


def _load(relative: str) -> object:
    """The script at `relative`, imported under a name of its own.

    Cached the way `import` caches, so a module holding state built at
    import time — `demo/adc_feeder.py` builds its channel list once —
    behaves across tests the way it behaves in the process that runs it.

    Returned as `object` rather than `ModuleType` so each accessor below
    can name the surface it expects. A module and a protocol do not
    overlap as far as the checker is concerned, and widening here is what
    makes those casts a claim about the script rather than a mistake.
    """
    path = path_to(relative)
    name = f"_shipped_{path.stem}"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable
        raise ImportError(f"no import machinery for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_as_main(relative: str) -> dict[str, object]:
    """Execute the script the way the shell does, guard block included.

    `if __name__ == "__main__":` is not boilerplate in these files: it is
    where the sensor templates choose their identifiers and where the
    feeder installs the log format the demo is read through. Running it
    is the only way to check it, and the only way it is measured.

    Every dependency it reaches for has to be replaced first — each of
    these blocks otherwise opens a socket or writes to InfluxDB.
    """
    return cast(
        dict[str, object], runpy.run_path(str(path_to(relative)), run_name="__main__")
    )


# --------------------------------------------------------------------------
# templates/custom-sensor/
# --------------------------------------------------------------------------

SENSOR_CONTINUOUS = "templates/custom-sensor/sensor_continuous.py"
SENSOR_TRIGGERED = "templates/custom-sensor/sensor_triggered.py"


class SensorTemplate(Protocol):
    """What both copy-me templates expose before anyone edits them."""

    read_value: Callable[[], float | None]


def sensor_template(relative: str) -> SensorTemplate:
    return cast(SensorTemplate, _load(relative))


# --------------------------------------------------------------------------
# demo/adc_feeder.py
# --------------------------------------------------------------------------

ADC_FEEDER = "demo/adc_feeder.py"


class Signal(Protocol):
    """One analog input, as the feeder's own `Signal` protocol has it."""

    name: str

    def volts_at(self, elapsed: float, /) -> float: ...


class Periodic(Signal, Protocol):
    """A `Channel`: slow drift plus noise, staggered by a random phase."""

    _phase: float


class Walk(Signal, Protocol):
    """A `Wander`: a mean-reverting random walk holding its own position."""

    _volts: float


class AdcFeeder(Protocol):
    """The demo's stand-in for an ADC board."""

    VREF: float
    FULL_SCALE: int
    SAMPLE_INTERVAL_SECONDS: float
    CHANNELS: tuple[Signal, ...]

    _counts: Callable[[float], float]
    Channel: Callable[..., Periodic]
    Wander: Callable[..., Walk]
    serve: Callable[..., None]
    _UtcMilliseconds: Callable[[], logging.Formatter]


def adc_feeder() -> AdcFeeder:
    return cast(AdcFeeder, _load(ADC_FEEDER))


# --------------------------------------------------------------------------
# scripts/
# --------------------------------------------------------------------------

SMOKE_LOGS = "scripts/smoke_logs.py"
SMOKE_DASHBOARD = "scripts/smoke_dashboard.py"


class SmokeLogs(Protocol):
    """The check that Loki has a line from every running container."""

    _Rejected: type[Exception]
    _running_containers: Callable[[], set[str]]
    _tls_context: Callable[[str | None], ssl.SSLContext | None]
    _collected_containers: Callable[[str, str, ssl.SSLContext | None], set[str]]
    main: Callable[[], int]


def smoke_logs() -> SmokeLogs:
    return cast(SmokeLogs, _load(SMOKE_LOGS))


class Query(Protocol):
    """One panel target, ready to send and to report on."""

    label: str
    payload: dict[str, object]
    may_be_empty: bool


class SmokeDashboard(Protocol):
    """The check that every dashboard panel still returns rows."""

    _Rejected: type[Exception]
    _Query: Callable[..., Query]
    _LOG_LINE_LIMIT: int
    _DASHBOARD_DIR: Path

    _mapping: Callable[[object], dict[str, object]]
    _mappings: Callable[[object], list[dict[str, object]]]
    _values_of: Callable[[dict[str, object]], list[str]]
    _interpolate: Callable[[str, dict[str, list[str]]], str]
    _child: Callable[[object, str], object]
    _first: Callable[[object], object]
    _row_count: Callable[[object], int]
    _payload: Callable[
        [str, dict[str, object], dict[str, list[str]]], dict[str, object]
    ]
    _load_queries: Callable[[Path, dict[str, object]], list[Query]]
    _selected: Callable[[list[str] | None], list[Path]]
    _tls_context: Callable[[str | None], ssl.SSLContext | None]
    _post: Callable[
        [dict[str, object], str, str, ssl.SSLContext | None], dict[str, object]
    ]
    _attempt: Callable[..., list[tuple[str, str]]]
    _report: Callable[[list[tuple[str, str]], list[Query], float, int], None]
    main: Callable[[], int]


def smoke_dashboard() -> SmokeDashboard:
    return cast(SmokeDashboard, _load(SMOKE_DASHBOARD))
