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
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/somewhere")

    assert cache_path() == Path("/tmp/somewhere/labmon/sensors.json")


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
    save(target, {"cryo-77k": _known("cryo-77k")})

    restored = load(target)

    assert restored["cryo-77k"].unit == "K"
    assert restored["cryo-77k"].last_seen == _NOW


def test_saving_creates_the_directory(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "down" / "sensors.json"

    save(target, {"a": _known("a")})

    assert target.is_file()


def test_the_cache_is_readable_json(tmp_path: Path) -> None:
    # Somebody should be able to look at it and delete a line.
    target = tmp_path / "sensors.json"
    save(target, {"a": _known("a")})

    assert "sensor_id" in json.loads(target.read_text())["a"]


def test_merging_keeps_a_sensor_the_live_query_did_not_return() -> None:
    # The whole reason the cache exists: a sensor silent for longer than
    # the window has no row to be stale, so it vanishes rather than
    # turning red.
    cached = {"gone": _known("gone", seconds_ago=90_000)}

    merged = merge(cached, [_known("here")])

    assert set(merged) == {"gone", "here"}


def test_merging_prefers_the_live_reading() -> None:
    cached = {"a": _known("a", seconds_ago=90_000)}

    merged = merge(cached, [_known("a", seconds_ago=1)])

    assert merged["a"].last_seen == _NOW - timedelta(seconds=1)


def test_merging_adds_a_sensor_the_cache_had_never_seen() -> None:
    # A cache used as a substitute rather than a union would hide a newly
    # added sensor until somebody remembered to rebuild it.
    merged = merge({}, [_known("brand-new")])

    assert "brand-new" in merged


def test_forgetting_removes_one_sensor() -> None:
    cached = {"a": _known("a"), "b": _known("b")}

    assert set(forget(cached, "a")) == {"b"}


def test_forgetting_an_unknown_sensor_says_so() -> None:
    with pytest.raises(KeyError):
        _ = forget({"a": _known("a")}, "nope")


def test_forgetting_does_not_mutate_what_it_was_given() -> None:
    cached = {"a": _known("a"), "b": _known("b")}

    _ = forget(cached, "a")

    assert set(cached) == {"a", "b"}


def test_a_cache_that_is_not_an_object_reads_as_empty(tmp_path: Path) -> None:
    target = tmp_path / "sensors.json"
    _ = target.write_text("[1, 2, 3]")

    assert load(target) == {}


def test_an_entry_that_is_not_an_object_is_skipped(tmp_path: Path) -> None:
    target = tmp_path / "sensors.json"
    _ = target.write_text(json.dumps({"a": "not an object"}))

    assert load(target) == {}


def test_an_entry_missing_a_field_is_skipped(tmp_path: Path) -> None:
    # Hand-editing this file is expected, so half an entry should cost
    # that entry rather than the whole roster.
    target = tmp_path / "sensors.json"
    _ = target.write_text(
        json.dumps(
            {
                "broken": {"sensor_id": "broken"},
                "intact": {
                    "sensor_id": "intact",
                    "measurement": "temperature",
                    "unit": "K",
                    "last_seen": _NOW.isoformat(),
                },
            }
        )
    )

    assert set(load(target)) == {"intact"}


def test_an_entry_with_an_unreadable_timestamp_is_skipped(tmp_path: Path) -> None:
    target = tmp_path / "sensors.json"
    _ = target.write_text(
        json.dumps(
            {
                "broken": {
                    "sensor_id": "broken",
                    "measurement": "temperature",
                    "unit": "K",
                    "last_seen": "the day before yesterday",
                }
            }
        )
    )

    assert load(target) == {}


def test_an_empty_roster_renders_as_a_sentence() -> None:
    from labmon.cli.render import render_roster

    empty: set[str] = set()

    assert render_roster({}, live=empty, now=_NOW) == "nothing remembered yet"
