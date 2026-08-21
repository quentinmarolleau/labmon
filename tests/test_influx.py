import logging
from pathlib import Path

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


# --------------------------------------------------------------------------
# Verifying the server against a private CA
# --------------------------------------------------------------------------


def test_no_ca_is_passed_when_the_variable_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default path must stay byte-identical to plain HTTP.

    A stack that has not adopted the `tls` profile should be unaffected by
    this option existing, so nothing is added to the client's arguments
    unless somebody asked for it.
    """
    monkeypatch.setenv("INFLUXDB3_AUTH_TOKEN", "test-token")
    monkeypatch.delenv("INFLUXDB_TLS_CA", raising=False)
    captured: dict[str, object] = {}

    def capture(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(influx, "InfluxDBClient3", capture)
    _ = get_client()

    assert "ssl_ca_cert" not in captured


def test_the_ca_is_passed_when_the_variable_names_a_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One kwarg covers both paths.

    `ssl_ca_cert` is read as a file path by the HTTP write client and by
    the Flight SQL query client alike, so a single setting is enough to
    verify the server on both.
    """
    monkeypatch.setenv("INFLUXDB3_AUTH_TOKEN", "test-token")
    ca = tmp_path / "labmon-ca.crt"
    _ = ca.write_text("-- not parsed here --", encoding="utf-8")
    monkeypatch.setenv("INFLUXDB_TLS_CA", str(ca))
    captured: dict[str, object] = {}

    def capture(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(influx, "InfluxDBClient3", capture)
    _ = get_client()

    assert captured["ssl_ca_cert"] == str(ca)


def test_a_missing_ca_file_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail where the mistake is, not on the first write.

    Left to the TLS library, a path that does not exist surfaces as a
    verification error against the *server* — which reads as a server or
    certificate problem rather than as a typo in one client's env file.
    """
    monkeypatch.setenv("INFLUXDB3_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("INFLUXDB_TLS_CA", str(tmp_path / "absent.crt"))

    with pytest.raises(FileNotFoundError, match="INFLUXDB_TLS_CA"):
        _ = get_client()


def _unused_client(**_kwargs: object) -> object:
    """Stand in for the real client, which these tests never talk to."""
    return object()


def test_a_ca_against_a_plain_http_host_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The one way this setting fails quietly.

    A CA is accepted and then ignored when the host is plain HTTP, so the
    traffic stays in the clear while the operator believes otherwise.
    """
    monkeypatch.setenv("INFLUXDB3_AUTH_TOKEN", "test-token")
    ca = tmp_path / "labmon-ca.crt"
    _ = ca.write_text("-- not parsed here --", encoding="utf-8")
    monkeypatch.setenv("INFLUXDB_TLS_CA", str(ca))
    monkeypatch.setenv("INFLUXDB_HOST", "http://localhost:8181")
    monkeypatch.setattr(influx, "InfluxDBClient3", _unused_client)

    with caplog.at_level(logging.WARNING, logger=influx.__name__):
        _ = get_client()

    assert [record.getMessage() for record in caplog.records] == [
        "ignoring INFLUXDB_TLS_CA because the host is not https"
    ]


def test_a_ca_against_an_https_host_is_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("INFLUXDB3_AUTH_TOKEN", "test-token")
    ca = tmp_path / "labmon-ca.crt"
    _ = ca.write_text("-- not parsed here --", encoding="utf-8")
    monkeypatch.setenv("INFLUXDB_TLS_CA", str(ca))
    monkeypatch.setenv("INFLUXDB_HOST", "HTTPS://server:8443")
    monkeypatch.setattr(influx, "InfluxDBClient3", _unused_client)

    with caplog.at_level(logging.WARNING, logger=influx.__name__):
        _ = get_client()

    assert caplog.records == []


# --------------------------------------------------------------------------
# Settings are read per call, not per import
# --------------------------------------------------------------------------


def test_the_host_is_read_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """The point of #119.

    `labmon.influx` is imported long before most callers configure
    anything, so a value read at import is whatever happened to be set at
    that moment rather than what the caller asked for.
    """
    monkeypatch.setenv("INFLUXDB_HOST", "http://first:8181")
    assert influx_host() == "http://first:8181"

    monkeypatch.setenv("INFLUXDB_HOST", "http://second:8181")
    assert influx_host() == "http://second:8181"


def test_the_database_is_read_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFLUXDB_DATABASE", "first")
    assert influx_database() == "first"

    monkeypatch.setenv("INFLUXDB_DATABASE", "second")
    assert influx_database() == "second"


def test_both_fall_back_when_unset_or_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty counts as unset, the way Compose reads the same variables."""
    monkeypatch.delenv("INFLUXDB_HOST", raising=False)
    monkeypatch.setenv("INFLUXDB_DATABASE", "")

    assert influx_host() == "http://localhost:8181"
    assert influx_database() == "lab"


def test_the_client_follows_the_environment_between_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two clients built either side of a change disagree, as they should."""
    monkeypatch.setenv("INFLUXDB3_AUTH_TOKEN", "test-token")
    captured: list[dict[str, object]] = []

    def capture(**kwargs: object) -> object:
        captured.append(kwargs)
        return object()

    monkeypatch.setattr(influx, "InfluxDBClient3", capture)

    monkeypatch.setenv("INFLUXDB_HOST", "http://first:8181")
    monkeypatch.setenv("INFLUXDB_DATABASE", "first")
    _ = get_client()

    monkeypatch.setenv("INFLUXDB_HOST", "http://second:8181")
    monkeypatch.setenv("INFLUXDB_DATABASE", "second")
    _ = get_client()

    assert [(call["host"], call["database"]) for call in captured] == [
        ("http://first:8181", "first"),
        ("http://second:8181", "second"),
    ]
