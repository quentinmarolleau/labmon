"""The configure API calls behind `labmon init` and `reset-database`.

Every request is faked at `urllib.request.urlopen`. What is asserted is
the shape labmon sends and what it makes of each answer — particularly
the statuses that are not failures: a 409 means the thing is already
there, which is the ordinary result of running `init` twice.

The real endpoints are exercised by the smoke job, which runs `init`
against a live server as the quickstart does.
"""

import io
import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from typing import Self, cast

import certifi
import pytest

from labmon import admin


class _Recorder:
    """Stands in for `urlopen`, keeping the requests it was given."""

    def __init__(self, *answers: object) -> None:
        self.answers: list[object] = list(answers)
        self.requests: list[urllib.request.Request] = []
        self.contexts: list[ssl.SSLContext | None] = []

    def __call__(self, request: urllib.request.Request, **kwargs: object) -> object:
        self.requests.append(request)
        self.contexts.append(cast("ssl.SSLContext | None", kwargs.get("context")))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    @property
    def sent(self) -> dict[str, object]:
        """The JSON body of the single request made."""
        assert len(self.requests) == 1, f"wanted one request, got {len(self.requests)}"
        return cast(dict[str, object], json.loads(cast(bytes, self.requests[0].data)))


def _response(payload: object, status: int = 200) -> object:
    """Something `urlopen` could have returned for a successful call."""
    body = json.dumps(payload).encode()

    class _Response:
        def __init__(self) -> None:
            self.status: int = status

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return body

    return _Response()


def _http_error(status: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://x", status, "nope", Message(), io.BytesIO(body.encode())
    )


Install = Callable[..., _Recorder]


@pytest.fixture
def urlopen(monkeypatch: pytest.MonkeyPatch) -> Install:
    """Install a recorder in place of the network, per test."""

    def install(*answers: object) -> _Recorder:
        recorder = _Recorder(*answers)
        monkeypatch.setattr("urllib.request.urlopen", recorder)
        return recorder

    return install


class TestAdminToken:
    """The one unauthenticated call, which bootstraps the rest."""

    def test_a_fresh_instance_issues_a_token(self, urlopen: Install) -> None:
        recorder = urlopen(_response({"token": "apiv3_abc"}))

        assert admin.create_admin_token("http://influx:8181") == "apiv3_abc"

        [request] = recorder.requests
        assert request.full_url == "http://influx:8181/api/v3/configure/token/admin"
        assert request.get_method() == "POST"
        # No credential: this is the call that issues one.
        assert request.get_header("Authorization") is None

    def test_a_trailing_slash_on_the_host_does_not_double_up(
        self, urlopen: Install
    ) -> None:
        recorder = urlopen(_response({"token": "apiv3_abc"}))

        _ = admin.create_admin_token("http://influx:8181/")

        assert recorder.requests[0].full_url.count("//") == 1

    def test_an_initialised_instance_reports_no_token_rather_than_failing(
        self, urlopen: Install
    ) -> None:
        """409 is the ordinary answer to running `init` a second time."""
        _ = urlopen(_http_error(409, "token name already exists, _admin"))

        assert admin.create_admin_token("http://influx:8181") is None

    def test_another_failure_carries_the_server_s_own_words(
        self, urlopen: Install
    ) -> None:
        _ = urlopen(_http_error(500, "catalog unavailable"))

        with pytest.raises(admin.AdminError, match="catalog unavailable"):
            _ = admin.create_admin_token("http://influx:8181")

    def test_a_token_with_no_value_is_refused(self, urlopen: Install) -> None:
        """Better than writing an empty credential into `.env`."""
        _ = urlopen(_response({"token": ""}))

        with pytest.raises(admin.AdminError, match="no value in it"):
            _ = admin.create_admin_token("http://influx:8181")

    def test_an_unreachable_host_names_itself(self, urlopen: Install) -> None:
        """The usual cause is a command run against the wrong host."""
        _ = urlopen(urllib.error.URLError("connection refused"))

        with pytest.raises(admin.AdminError, match="cannot reach http://influx:8181"):
            _ = admin.create_admin_token("http://influx:8181")


class TestDatabase:
    """Create, update and delete, and what each status means."""

    def test_creating_sends_the_name_and_retention(self, urlopen: Install) -> None:
        recorder = urlopen(_response({}))

        assert admin.create_database("http://x", "tok", "lab", "1y") is True

        assert recorder.sent == {"db": "lab", "retention_period": "1y"}
        assert recorder.requests[0].get_header("Authorization") == "Bearer tok"

    def test_no_retention_is_sent_as_null(self, urlopen: Install) -> None:
        """Which the server reads as unlimited, matching an auto-created one."""
        recorder = urlopen(_response({}))

        _ = admin.create_database("http://x", "tok", "lab", None)

        assert recorder.sent == {"db": "lab", "retention_period": None}

    def test_an_existing_database_is_not_an_error(self, urlopen: Install) -> None:
        _ = urlopen(_http_error(409, "attempted to create a resource that exists"))

        assert admin.create_database("http://x", "tok", "lab", None) is False

    def test_a_rejected_retention_quotes_the_server(self, urlopen: Install) -> None:
        """The server says `expected a duration`, which beats anything here."""
        _ = urlopen(_http_error(400, 'invalid value: string "banana"'))

        with pytest.raises(admin.AdminError, match="banana"):
            _ = admin.create_database("http://x", "tok", "lab", "banana")

    def test_setting_retention_uses_put(self, urlopen: Install) -> None:
        recorder = urlopen(_response({}))

        admin.set_retention("http://x", "tok", "lab", "30d")

        assert recorder.requests[0].get_method() == "PUT"
        assert recorder.sent == {"db": "lab", "retention_period": "30d"}

    def test_a_refused_retention_change_is_reported(self, urlopen: Install) -> None:
        _ = urlopen(_http_error(404, "database not found"))

        with pytest.raises(admin.AdminError, match="database not found"):
            admin.set_retention("http://x", "tok", "lab", "30d")

    def test_deleting_is_soft_by_default(self, urlopen: Install) -> None:
        """The renamed copy is what makes an accidental reset recoverable."""
        recorder = urlopen(_response({}))

        admin.delete_database("http://x", "tok", "lab")

        url = recorder.requests[0].full_url
        assert recorder.requests[0].get_method() == "DELETE"
        assert "db=lab" in url
        assert "hard_delete_at" not in url

    def test_a_hard_delete_asks_for_the_space_now(self, urlopen: Install) -> None:
        recorder = urlopen(_response({}))

        admin.delete_database("http://x", "tok", "lab", hard=True)

        assert "hard_delete_at=now" in recorder.requests[0].full_url

    def test_a_refused_delete_is_reported(self, urlopen: Install) -> None:
        _ = urlopen(_http_error(404, "database not found"))

        with pytest.raises(admin.AdminError, match="could not delete"):
            admin.delete_database("http://x", "tok", "lab")


# What `SELECT ... FROM system.databases` answers: one row with a
# retention, one without (created by a write), and the server's own.
_ROWS = [
    {"database_name": "_internal", "retention_period_ns": 604_800_000_000_000},
    {"database_name": "lab", "retention_period_ns": 2_592_000_000_000_000},
    {"database_name": "scratch"},
]


class TestReadingTheCatalogue:
    """Retention and existence, read from `_internal`.

    The configure endpoint lists names and nothing else, so this is the
    only way to learn what retention a database has — which a reset needs
    before it destroys the database holding it.
    """

    def test_retention_comes_back_in_days(self, urlopen: Install) -> None:
        _ = urlopen(_response(_ROWS))

        assert admin.read_retention("http://x", "tok", "lab") == "30d"

    def test_a_year_survives_the_round_trip(self, urlopen: Install) -> None:
        """The server stores `1y` as 365.25 days; a reset puts back 365d.

        The quarter day is InfluxDB's own definition of a year rather
        than anything labmon chose, and carrying it through a reset would
        mean inventing a duration the user never typed.
        """
        rows = [{"database_name": "lab", "retention_period_ns": 31_557_600_000_000_000}]
        _ = urlopen(_response(rows))

        assert admin.read_retention("http://x", "tok", "lab") == "365d"

    def test_an_unlimited_database_reports_none(self, urlopen: Install) -> None:
        """A database a write created carries no retention at all."""
        _ = urlopen(_response(_ROWS))

        assert admin.read_retention("http://x", "tok", "scratch") is None

    def test_an_unknown_database_reports_none(self, urlopen: Install) -> None:
        _ = urlopen(_response(_ROWS))

        assert admin.read_retention("http://x", "tok", "absent") is None

    def test_a_sub_day_retention_does_not_round_to_nothing(
        self, urlopen: Install
    ) -> None:
        """`24h` is a day; anything shorter would otherwise become `0d`.

        A reset that put back `0d` would ask the server to keep nothing,
        which is a far worse answer than keeping a day too much.
        """
        rows = [{"database_name": "lab", "retention_period_ns": 3_600_000_000_000}]
        _ = urlopen(_response(rows))

        assert admin.read_retention("http://x", "tok", "lab") == "1d"

    def test_the_query_ignores_tombstones(self, urlopen: Install) -> None:
        """A soft delete leaves a `<name>-<timestamp>` row marked deleted.

        Without the filter, a database dropped and recreated would be
        reported twice and the wrong row could answer first.
        """
        recorder = urlopen(_response(_ROWS))

        _ = admin.read_retention("http://x", "tok", "lab")

        assert "deleted+%3D+false" in recorder.requests[0].full_url

    def test_existence_is_read_from_the_same_place(self, urlopen: Install) -> None:
        _ = urlopen(_response(_ROWS))

        assert admin.database_exists("http://x", "tok", "lab") is True

    def test_a_database_that_is_not_there(self, urlopen: Install) -> None:
        _ = urlopen(_response(_ROWS))

        assert admin.database_exists("http://x", "tok", "absent") is False

    def test_a_failed_listing_is_reported(self, urlopen: Install) -> None:
        _ = urlopen(_http_error(500, "catalog unavailable"))

        with pytest.raises(admin.AdminError, match="could not list the databases"):
            _ = admin.database_exists("http://x", "tok", "lab")

    def test_a_failed_retention_read_is_reported(self, urlopen: Install) -> None:
        _ = urlopen(_http_error(500, "catalog unavailable"))

        with pytest.raises(admin.AdminError, match="could not read the current"):
            _ = admin.read_retention("http://x", "tok", "lab")


class TestTls:
    """`INFLUXDB_TLS_CA`, so the `tls` profile needs configuring once."""

    def test_no_ca_uses_the_system_store(
        self, urlopen: Install, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("INFLUXDB_TLS_CA", raising=False)
        recorder = urlopen(_response({"token": "apiv3_a"}))

        _ = admin.create_admin_token("https://caddy:8443")

        assert recorder.contexts == [None]

    def test_a_ca_is_trusted(
        self, urlopen: Install, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        certificate = tmp_path / "ca.crt"
        _ = certificate.write_bytes(Path(certifi.where()).read_bytes())
        monkeypatch.setenv("INFLUXDB_TLS_CA", str(certificate))
        recorder = urlopen(_response({"token": "apiv3_a"}))

        _ = admin.create_admin_token("https://caddy:8443")

        [context] = recorder.contexts
        assert isinstance(context, ssl.SSLContext)
        assert context.get_ca_certs()

    def test_a_ca_that_is_not_a_file_names_the_variable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The same message `get_client` gives, for the same typo."""
        monkeypatch.setenv("INFLUXDB_TLS_CA", str(tmp_path / "absent.crt"))

        with pytest.raises(admin.AdminError, match="INFLUXDB_TLS_CA points at"):
            _ = admin.create_admin_token("https://caddy:8443")
