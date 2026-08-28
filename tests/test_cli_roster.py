"""The remembered list of sensors, and where it lives on disk."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from labmon.cli.roster import Known, cache_path, forget, load, merge, save

_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def _known(sensor: str, seconds_ago: int = 0) -> Known:
    return Known(
        sensor_id=sensor,
        measurement="temperature",
        unit="K",
        last_seen=_NOW - timedelta(seconds=seconds_ago),
    )


def test_the_cache_honours_xdg_cache_home(monkeypatch: pytest.MonkeyPatch) -> None:
    # Named, never created. Somewhere other than /tmp, which would be a
    # real shared directory and is read as one by anything looking for
    # insecure temporary paths.
    monkeypatch.setenv("XDG_CACHE_HOME", "/nowhere/cache")

    assert cache_path() == Path("/nowhere/cache/labmon/sensors.json")


def test_the_cache_falls_back_to_dot_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/someone")

    assert cache_path() == Path("/home/someone/.cache/labmon/sensors.json")


def test_it_is_a_cache_not_a_config(monkeypatch: pytest.MonkeyPatch) -> None:
    # Derived data that can be deleted at any time without losing a
    # setting, so it belongs beside other caches rather than in config.
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/someone")

    assert ".config" not in str(cache_path())


def test_a_missing_cache_reads_as_empty(tmp_path: Path) -> None:
    assert load(tmp_path / "absent.json") == {}


def test_a_corrupt_cache_reads_as_empty_rather_than_failing(tmp_path: Path) -> None:
    # A cache is rebuildable by definition. Refusing to run because a
    # derived file was truncated would be the wrong trade.
    target = tmp_path / "sensors.json"
    _ = target.write_text("{not json at all")

    assert load(target) == {}


def test_what_is_saved_comes_back(tmp_path: Path) -> None:
    target = tmp_path / "sensors.json"
    save(target, merge({}, [_known("cryo-77k")]))

    restored = load(target)

    assert restored[("cryo-77k", "temperature")].unit == "K"
    assert restored[("cryo-77k", "temperature")].last_seen == _NOW


def test_saving_creates_the_directory(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "down" / "sensors.json"

    save(target, merge({}, [_known("a")]))

    assert target.is_file()


def test_the_cache_is_readable_json(tmp_path: Path) -> None:
    # Somebody should be able to look at it and delete a line.
    target = tmp_path / "sensors.json"
    save(target, merge({}, [_known("a")]))

    assert "sensor_id" in json.loads(target.read_text())[0]


def test_merging_keeps_a_sensor_the_live_query_did_not_return() -> None:
    # The whole reason the cache exists: a sensor silent for longer than
    # the window has no row to be stale, so it vanishes rather than
    # turning red.
    cached = merge({}, [_known("gone", seconds_ago=90_000)])

    merged = merge(cached, [_known("here")])

    assert {entry.sensor_id for entry in merged.values()} == {"gone", "here"}


def test_merging_prefers_the_live_reading() -> None:
    cached = merge({}, [_known("a", seconds_ago=90_000)])

    merged = merge(cached, [_known("a", seconds_ago=1)])

    assert merged[("a", "temperature")].last_seen == _NOW - timedelta(seconds=1)


def test_merging_adds_a_sensor_the_cache_had_never_seen() -> None:
    # A cache used as a substitute rather than a union would hide a newly
    # added sensor until somebody remembered to rebuild it.
    merged = merge({}, [_known("brand-new")])

    assert ("brand-new", "temperature") in merged


def test_forgetting_removes_one_sensor() -> None:
    cached = merge({}, [_known("a"), _known("b")])

    assert {e.sensor_id for e in forget(cached, "a").values()} == {"b"}


def test_forgetting_an_unknown_sensor_says_so() -> None:
    with pytest.raises(KeyError):
        _ = forget(merge({}, [_known("a")]), "nope")


def test_forgetting_does_not_mutate_what_it_was_given() -> None:
    cached = merge({}, [_known("a"), _known("b")])

    _ = forget(cached, "a")

    assert {e.sensor_id for e in cached.values()} == {"a", "b"}


def test_a_cache_that_is_not_an_object_reads_as_empty(tmp_path: Path) -> None:
    target = tmp_path / "sensors.json"
    _ = target.write_text('{"a": 1}')

    assert load(target) == {}


def test_an_entry_that_is_not_an_object_is_skipped(tmp_path: Path) -> None:
    target = tmp_path / "sensors.json"
    _ = target.write_text(json.dumps(["not an object"]))

    assert load(target) == {}


def test_an_entry_missing_a_field_is_skipped(tmp_path: Path) -> None:
    # Hand-editing this file is expected, so half an entry should cost
    # that entry rather than the whole roster.
    target = tmp_path / "sensors.json"
    _ = target.write_text(
        json.dumps(
            [
                {"sensor_id": "broken"},
                {
                    "sensor_id": "intact",
                    "measurement": "temperature",
                    "unit": "K",
                    "last_seen": _NOW.isoformat(),
                },
            ]
        )
    )

    assert {e.sensor_id for e in load(target).values()} == {"intact"}


def test_an_entry_with_an_unreadable_timestamp_is_skipped(tmp_path: Path) -> None:
    target = tmp_path / "sensors.json"
    _ = target.write_text(
        json.dumps(
            [
                {
                    "sensor_id": "broken",
                    "measurement": "temperature",
                    "unit": "K",
                    "last_seen": "the day before yesterday",
                }
            ]
        )
    )

    assert load(target) == {}


def test_an_empty_roster_renders_as_a_sentence() -> None:
    from labmon.cli.render import render_roster

    empty: set[str] = set()

    assert render_roster({}, live=empty, now=_NOW) == "nothing remembered yet"


def _at(sensor: str, measurement: str, unit: str) -> Known:
    return Known(sensor_id=sensor, measurement=measurement, unit=unit, last_seen=_NOW)


def test_a_sensor_reporting_two_measurements_keeps_both() -> None:
    # Keying by sensor alone kept whichever row iterated last, and the
    # union's row order is not defined — so which one survived was
    # arbitrary. `fetch_latest` returns a row per sensor and measurement,
    # and the roster has to hold the same shape.
    merged = merge(
        {}, [_at("multi", "temperature", "K"), _at("multi", "pressure", "mbar")]
    )

    assert {entry.measurement for entry in merged.values()} == {
        "temperature",
        "pressure",
    }


def test_the_same_sensor_and_measurement_is_updated_not_duplicated() -> None:
    first = _at("a", "temperature", "K")
    later = Known(
        sensor_id="a",
        measurement="temperature",
        unit="K",
        last_seen=_NOW + timedelta(seconds=10),
    )

    merged = merge(merge({}, [first]), [later])

    assert len(merged) == 1
    assert next(iter(merged.values())).last_seen == later.last_seen


def test_both_measurements_survive_a_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "sensors.json"
    save(
        target,
        merge({}, [_at("multi", "temperature", "K"), _at("multi", "pressure", "mbar")]),
    )

    assert len(load(target)) == 2


def test_forgetting_a_sensor_forgets_all_of_its_measurements() -> None:
    known = merge(
        {},
        [
            _at("multi", "temperature", "K"),
            _at("multi", "pressure", "mbar"),
            _at("other", "temperature", "K"),
        ],
    )

    remaining = forget(known, "multi")

    assert {entry.sensor_id for entry in remaining.values()} == {"other"}


def test_a_failed_write_leaves_the_previous_roster_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The point of writing through a temporary file: a half-written
    # roster reads as empty, and what that heals to is the loss of every
    # quiet sensor this exists to remember.
    target = tmp_path / "sensors.json"
    save(target, merge({}, [_known("keep-me")]))

    def refuse(_self: Path, *_args: object, **_kwargs: object) -> int:
        raise OSError("no space left on device")

    monkeypatch.setattr(Path, "write_text", refuse)

    with pytest.raises(OSError, match="no space"):
        save(target, merge({}, [_known("new")]))

    monkeypatch.undo()
    assert {entry.sensor_id for entry in load(target).values()} == {"keep-me"}


def test_a_failed_write_does_not_leave_a_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "sensors.json"

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr("os.replace", refuse)

    with pytest.raises(OSError):
        save(target, merge({}, [_known("a")]))

    monkeypatch.undo()
    assert list(tmp_path.glob("*.tmp")) == []


# --------------------------------------------------------------------------
# The reading an entry was last heard saying
# --------------------------------------------------------------------------


def test_a_remembered_reading_survives_the_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "sensors.json"
    save(target, merge({}, [Known("cryo", "temperature", "K", _NOW, 4.2)]))

    assert load(target)[("cryo", "temperature")].value == 4.2


def test_an_entry_written_before_readings_were_remembered_still_loads(
    tmp_path: Path,
) -> None:
    # The cache predates the field, and an entry is worth keeping for
    # its identity and its timestamp whether or not it has a reading.
    target = tmp_path / "sensors.json"
    _ = target.write_text(
        json.dumps(
            [
                {
                    "sensor_id": "cryo",
                    "measurement": "temperature",
                    "unit": "K",
                    "last_seen": _NOW.isoformat(),
                }
            ]
        )
    )

    entry = load(target)[("cryo", "temperature")]

    assert entry.value is None
    assert entry.unit == "K"


@pytest.mark.parametrize("written", ["4.2", None, True, [4.2], {}])
def test_a_reading_that_is_not_a_number_is_dropped(
    tmp_path: Path, written: object
) -> None:
    # Hand-edited files reach here. Refusing the whole entry would lose
    # a sensor over a field it can do without.
    target = tmp_path / "sensors.json"
    _ = target.write_text(
        json.dumps(
            [
                {
                    "sensor_id": "cryo",
                    "measurement": "temperature",
                    "unit": "K",
                    "last_seen": _NOW.isoformat(),
                    "value": written,
                }
            ]
        )
    )

    assert load(target)[("cryo", "temperature")].value is None


def test_an_integer_reading_is_kept_as_a_float(tmp_path: Path) -> None:
    # JSON writes 4.0 as 4, so a whole-numbered reading comes back as an
    # int and would otherwise be discarded on the next load.
    target = tmp_path / "sensors.json"
    _ = target.write_text(
        json.dumps(
            [
                {
                    "sensor_id": "cryo",
                    "measurement": "temperature",
                    "unit": "K",
                    "last_seen": _NOW.isoformat(),
                    "value": 4,
                }
            ]
        )
    )

    assert load(target)[("cryo", "temperature")].value == 4.0


def test_forgetting_a_sensor_keeps_the_others_readings(tmp_path: Path) -> None:
    target = tmp_path / "sensors.json"
    save(
        target,
        merge(
            {},
            [
                Known("cryo", "temperature", "K", _NOW, 4.2),
                Known("gauge", "pressure", "mbar", _NOW, 1e-7),
            ],
        ),
    )

    save(target, forget(load(target), "gauge"))

    assert load(target)[("cryo", "temperature")].value == 4.2
