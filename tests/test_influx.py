import pytest
from influxdb_client_3 import InfluxDBClient3

from labmon import influx
from labmon.influx import (
    _setting,  # pyright: ignore[reportPrivateUsage]
    get_client,
    influx_database,
    influx_host,
)

_NAME = "LABMON_TEST_SETTING"


def test_setting_prefers_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_NAME, "chosen")

    assert _setting(_NAME, "fallback") == "chosen"


def test_setting_falls_back_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_NAME, raising=False)

    assert _setting(_NAME, "fallback") == "fallback"


def test_setting_falls_back_when_set_but_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The reported bug. `.env.example` ships INFLUXDB_DATABASE empty and
    # Compose substitutes its default on empty as well as unset, so a
    # host-side script sourcing that file has to do the same or it selects
    # a database named "".
    monkeypatch.setenv(_NAME, "")

    assert _setting(_NAME, "fallback") == "fallback"


def test_get_client_raises_without_auth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INFLUXDB3_AUTH_TOKEN", raising=False)

    with pytest.raises(KeyError):
        _ = get_client()


def test_get_client_builds_client_with_auth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INFLUXDB3_AUTH_TOKEN", "test-token")

    client = get_client()

    assert isinstance(client, InfluxDBClient3)
    client.close()


@pytest.fixture
def client_kwargs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Record what get_client passes to InfluxDBClient3.

    The client stores its host inside a write client rather than on an
    attribute, so spying on the constructor is both simpler and less brittle
    than reading the object back.
    """
    calls: list[dict[str, str]] = []

    class _Spy:
        def __init__(self, **kwargs: str) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(influx, "InfluxDBClient3", _Spy)
    return calls


def test_get_client_reads_the_environment_when_called(
    monkeypatch: pytest.MonkeyPatch, client_kwargs: list[dict[str, str]]
) -> None:
    """The point of #119: settings resolve per call, not per import.

    labmon.influx is imported long before most callers configure anything, so
    a value read at import time is whatever happened to be set at that moment.
    """
    monkeypatch.setenv("INFLUXDB3_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("INFLUXDB_HOST", "http://first:8181")
    monkeypatch.setenv("INFLUXDB_DATABASE", "first")

    _ = get_client()

    monkeypatch.setenv("INFLUXDB_HOST", "http://second:8181")
    monkeypatch.setenv("INFLUXDB_DATABASE", "second")

    _ = get_client()

    assert client_kwargs[0]["host"] == "http://first:8181"
    assert client_kwargs[0]["database"] == "first"
    assert client_kwargs[1]["host"] == "http://second:8181"
    assert client_kwargs[1]["database"] == "second"


def test_get_client_uses_the_documented_defaults(
    monkeypatch: pytest.MonkeyPatch, client_kwargs: list[dict[str, str]]
) -> None:
    monkeypatch.setenv("INFLUXDB3_AUTH_TOKEN", "test-token")
    monkeypatch.delenv("INFLUXDB_HOST", raising=False)
    monkeypatch.delenv("INFLUXDB_DATABASE", raising=False)

    _ = get_client()

    assert client_kwargs[0]["host"] == "http://localhost:8181"
    assert client_kwargs[0]["database"] == "lab"


def test_get_client_falls_back_on_an_empty_value(
    monkeypatch: pytest.MonkeyPatch, client_kwargs: list[dict[str, str]]
) -> None:
    """`.env.example` ships INFLUXDB_DATABASE empty; Compose defaults it too."""
    monkeypatch.setenv("INFLUXDB3_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("INFLUXDB_DATABASE", "")

    _ = get_client()

    assert client_kwargs[0]["database"] == "lab"


def test_influx_host_reads_the_environment_each_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INFLUXDB_HOST", "http://first:8181")
    assert influx_host() == "http://first:8181"

    monkeypatch.setenv("INFLUXDB_HOST", "http://second:8181")
    assert influx_host() == "http://second:8181"


def test_influx_database_reads_the_environment_each_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INFLUXDB_DATABASE", "first")
    assert influx_database() == "first"

    monkeypatch.setenv("INFLUXDB_DATABASE", "second")
    assert influx_database() == "second"


def test_settings_fall_back_when_unset_or_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INFLUXDB_HOST", raising=False)
    monkeypatch.setenv("INFLUXDB_DATABASE", "")

    assert influx_host() == "http://localhost:8181"
    assert influx_database() == "lab"
