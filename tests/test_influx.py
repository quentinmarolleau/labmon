import pytest
from influxdb_client_3 import InfluxDBClient3

from labmon.influx import get_client


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
