"""The remembered list of sensors labmon has seen.

Derived data, kept beside other caches rather than in the config
directory: it can be deleted at any moment without losing a setting, and
rebuilding it costs one query.

It exists for one reason. A sensor silent for longer than the window
being asked about has no row in the result, so it has nothing to be
stale — it simply vanishes, which is the worst possible failure for a
view whose job is spotting silence. The cache is what remembers it.

That gives the rule the rest of the CLI has to honour: **the cache may
only add sensors, never replace or filter them.** Used as a union, a
stale cache is harmless. Used as a substitute, it acquires its own
silent failure, hiding a newly added sensor until somebody remembers to
rebuild.

Imports nothing heavy, so reaching for the roster does not pull the
database client into a path that has no use for it.
"""

import json
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

logger: logging.Logger = logging.getLogger(__name__)

CACHE_NAME = "sensors.json"


@dataclass(frozen=True)
class Known:
    """One sensor the roster remembers, and when it was last heard from."""

    sensor_id: str
    measurement: str
    unit: str
    last_seen: datetime


def cache_path() -> Path:
    """Where the roster lives, honouring `XDG_CACHE_HOME`."""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path(os.path.expanduser("~")) / ".cache"
    return root / "labmon" / CACHE_NAME


def load(path: Path) -> dict[str, Known]:
    """Read the roster, treating anything unreadable as empty.

    A cache is rebuildable by definition, so a truncated or hand-edited
    file is a reason to start again rather than a reason to refuse to
    run.
    """
    try:
        parsed = cast(object, json.loads(path.read_text()))
    except (OSError, ValueError):
        logger.debug("no usable roster cache", extra={"path": str(path)})
        return {}
    if not isinstance(parsed, dict):
        return {}

    known: dict[str, Known] = {}
    for sensor, entry in cast(dict[str, object], parsed).items():
        if not isinstance(entry, dict):
            continue
        fields = cast(dict[str, object], entry)
        try:
            known[str(sensor)] = Known(
                sensor_id=str(fields["sensor_id"]),
                measurement=str(fields["measurement"]),
                unit=str(fields["unit"]),
                last_seen=datetime.fromisoformat(str(fields["last_seen"])),
            )
        except (KeyError, ValueError):
            continue
    return known


def save(path: Path, known: Mapping[str, Known]) -> None:
    """Write the roster, creating its directory if need be.

    Indented rather than compact: somebody should be able to open it,
    see what is remembered and delete a line.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        sensor: {
            "sensor_id": entry.sensor_id,
            "measurement": entry.measurement,
            "unit": entry.unit,
            "last_seen": entry.last_seen.astimezone(UTC).isoformat(),
        }
        for sensor, entry in known.items()
    }
    _ = path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    logger.debug("wrote roster cache", extra={"sensors": len(payload)})


def merge(cached: Mapping[str, Known], live: Iterable[Known]) -> dict[str, Known]:
    """Everything the cache knows, updated by everything just seen.

    A union, never a replacement — see the module docstring. A live
    sensor wins over its cached entry because its reading is newer; a
    cached sensor the query did not return is kept, because that silence
    is the thing worth showing.
    """
    merged = dict(cached)
    for entry in live:
        merged[entry.sensor_id] = entry
    return merged


def forget(cached: Mapping[str, Known], sensor_id: str) -> dict[str, Known]:
    """The roster without `sensor_id`, for one that is genuinely gone.

    Raises `KeyError` when it was never there: silently succeeding would
    leave somebody believing they had removed a sensor that is still
    listed under a name they mistyped.
    """
    if sensor_id not in cached:
        raise KeyError(sensor_id)
    return {name: entry for name, entry in cached.items() if name != sensor_id}
