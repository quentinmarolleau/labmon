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
    """One sensor the roster remembers, and when it was last heard from.

    Identified by sensor *and* measurement, because a sensor may write
    to more than one table and `fetch_latest` returns a row for each
    pair. Keying on the sensor alone kept whichever arrived last, and
    the row order of a UNION is not defined — so which survived was
    arbitrary.
    """

    sensor_id: str
    measurement: str
    unit: str
    last_seen: datetime
    # The reading that was current when the sensor was last heard from.
    # Remembered so a sensor that has since gone quiet can still show
    # what it was reading, which is usually the question being asked of
    # it: an experiment that stopped reporting was doing *something* at
    # the moment it stopped. Optional, because a roster written before
    # this existed has none, and an entry is still worth keeping without
    # one.
    value: float | None = None

    @property
    def key(self) -> tuple[str, str]:
        """What identifies this entry in the roster."""
        return (self.sensor_id, self.measurement)


def _as_float(value: object) -> float | None:
    """A remembered reading, or `None` when there is not a usable one.

    Hand-edited and older roster files both reach here, so anything that
    is not a number is dropped rather than refused: the entry is still
    worth keeping for its identity and its timestamp.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def cache_path() -> Path:
    """Where the roster lives, honouring `XDG_CACHE_HOME`."""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path(os.path.expanduser("~")) / ".cache"
    return root / "labmon" / CACHE_NAME


def load(path: Path) -> dict[tuple[str, str], Known]:
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
    if not isinstance(parsed, list):
        return {}

    known: dict[tuple[str, str], Known] = {}
    for entry in cast(list[object], parsed):
        if not isinstance(entry, dict):
            continue
        fields = cast(dict[str, object], entry)
        try:
            found = Known(
                sensor_id=str(fields["sensor_id"]),
                measurement=str(fields["measurement"]),
                unit=str(fields["unit"]),
                last_seen=datetime.fromisoformat(str(fields["last_seen"])),
                value=_as_float(fields.get("value")),
            )
        except (KeyError, ValueError):
            continue
        known[found.key] = found
    return known


def save(path: Path, known: Mapping[tuple[str, str], Known]) -> None:
    """Write the roster, creating its directory if need be.

    A list rather than an object, since an entry is identified by two
    fields and a composite key would only be readable by splitting it.
    Indented rather than compact: somebody should be able to open this,
    see what is remembered, and delete an entry.

    Written to a neighbouring temporary file and moved into place. A
    crash midway through a direct write leaves a truncated file, which
    `load` correctly treats as empty — self-healing, except that what it
    heals to is an empty roster, discarding exactly the memory of quiet
    sensors this exists to keep. `os.replace` is atomic within a
    filesystem, so a reader sees either the old roster or the new one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: list[dict[str, object]] = [
        {
            "sensor_id": entry.sensor_id,
            "measurement": entry.measurement,
            "unit": entry.unit,
            "last_seen": entry.last_seen.astimezone(UTC).isoformat(),
            "value": entry.value,
        }
        for entry in sorted(known.values(), key=lambda item: item.key)
    ]
    scratch = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        _ = scratch.write_text(json.dumps(payload, indent=2) + "\n")
        os.replace(scratch, path)
    except OSError:
        scratch.unlink(missing_ok=True)
        raise
    logger.debug("wrote roster cache", extra={"sensors": len(payload)})


def merge(
    cached: Mapping[tuple[str, str], Known], live: Iterable[Known]
) -> dict[tuple[str, str], Known]:
    """Everything the cache knows, updated by everything just seen.

    A union, never a replacement — see the module docstring. A live
    sensor wins over its cached entry because its reading is newer; a
    cached sensor the query did not return is kept, because that silence
    is the thing worth showing.
    """
    merged = dict(cached)
    for entry in live:
        merged[entry.key] = entry
    return merged


def forget(
    cached: Mapping[tuple[str, str], Known], sensor_id: str
) -> dict[tuple[str, str], Known]:
    """The roster without `sensor_id`, for an instrument that is gone.

    Every measurement it wrote to goes with it: the thing being removed
    is a sensor, not one of the tables it happened to appear in.

    Raises `KeyError` when it was never there: silently succeeding would
    leave somebody believing they had removed a sensor that is still
    listed under a name they mistyped.
    """
    remaining = {
        key: entry for key, entry in cached.items() if entry.sensor_id != sensor_id
    }
    if len(remaining) == len(cached):
        raise KeyError(sensor_id)
    return remaining
