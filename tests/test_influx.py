import pytest
from influxdb_client_3 import InfluxDBClient3

from labmon.influx import (
    _setting,  # pyright: ignore[reportPrivateUsage]
    get_client,
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
