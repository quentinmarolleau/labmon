"""`labmon init` and `labmon reset-database`, driven through the CLI.

The admin calls are faked at `labmon.admin`, so what these check is the
command's own decisions: what it writes, what it refuses, and what it
says afterwards. `tests/test_admin.py` covers the requests themselves,
and the smoke job runs `init` against a live server.
"""

import logging
from pathlib import Path

import pytest

from labmon import admin, env
from labmon.cli.main import build_app
from tests.cli_runner import invoke


def _field(record: logging.LogRecord, name: str) -> object:
    """Read a logfmt field off a record.

    `extra=` fields become attributes a type checker cannot know about,
    the same way `tests/test_polling.py` reads them.
    """
    return getattr(record, name)  # pyright: ignore[reportAny]


def _already_initialised(_host: str) -> str | None:
    """A server that has issued its admin token already."""
    return None


def _already_exists(*_args: object, **_kwargs: object) -> bool:
    """A database that is already there."""
    return False


def _absent(*_args: object, **_kwargs: object) -> bool:
    """A database that is not."""
    return False


@pytest.fixture(autouse=True)
def _local_host(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host and no inherited token, whatever the machine running this.

    A contributor with direnv has a real token exported, and `env.load`
    deliberately lets the process environment win over `.env` — so
    without this the suite would read that token and these tests would
    pass or fail on somebody's personal setup.
    """
    monkeypatch.setenv("INFLUXDB_HOST", "http://influx:8181")
    monkeypatch.delenv("INFLUXDB3_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("INFLUXDB_DATABASE", raising=False)


class _Calls:
    """What the command asked the admin API to do, in order."""

    def __init__(self) -> None:
        self.made: list[tuple[object, ...]] = []

    def record(self, name: str, *args: object) -> None:
        self.made.append((name, *args))

    @property
    def names(self) -> list[str]:
        return [str(call[0]) for call in self.made]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> _Calls:
    """Fake the whole admin surface, recording what was asked of it."""
    recorded = _Calls()

    def create_admin_token(host: str) -> str | None:
        recorded.record("create_admin_token", host)
        return "apiv3_issued"

    def create_database(
        _host: str, _token: str, name: str, retention: str | None
    ) -> bool:
        recorded.record("create_database", name, retention)
        return True

    def set_retention(
        _host: str, _token: str, name: str, retention: str | None
    ) -> None:
        recorded.record("set_retention", name, retention)

    def delete_database(
        _host: str, _token: str, name: str, *, hard: bool = False
    ) -> None:
        recorded.record("delete_database", name, hard)

    def read_retention(_host: str, _token: str, name: str) -> str | None:
        recorded.record("read_retention", name)
        return "30d"

    def database_exists(_host: str, _token: str, name: str) -> bool:
        recorded.record("database_exists", name)
        return True

    monkeypatch.setattr(admin, "create_admin_token", create_admin_token)
    monkeypatch.setattr(admin, "create_database", create_database)
    monkeypatch.setattr(admin, "set_retention", set_retention)
    monkeypatch.setattr(admin, "delete_database", delete_database)
    monkeypatch.setattr(admin, "read_retention", read_retention)
    monkeypatch.setattr(admin, "database_exists", database_exists)
    return recorded


class TestInit:
    """The command that turns a bare instance into a working one."""

    def test_it_issues_a_token_saves_it_and_creates_the_database(
        self, calls: _Calls, tmp_path: Path
    ) -> None:
        result = invoke(build_app(), ["init", "--retention", "1y"])

        assert result.exit_code == 0
        assert calls.names == ["create_admin_token", "create_database"]
        assert calls.made[1] == ("create_database", "lab", "1y")
        written = (tmp_path / env.ENV_FILE).read_text(encoding="utf-8")
        assert "INFLUXDB3_AUTH_TOKEN=apiv3_issued" in written

    @pytest.mark.usefixtures("calls")
    def test_a_new_env_file_is_readable_only_by_its_owner(self, tmp_path: Path) -> None:
        """It holds a credential from the moment it exists.

        A world-readable token in a shared checkout is the kind of thing
        nobody notices until it matters.
        """
        _ = invoke(build_app(), ["init"])

        assert (tmp_path / env.ENV_FILE).stat().st_mode & 0o077 == 0

    def test_no_retention_asks_for_none(self, calls: _Calls) -> None:
        """Which is what a database created by a write gets anyway."""
        _ = invoke(build_app(), ["init"])

        assert calls.made[1] == ("create_database", "lab", None)

    def test_the_database_can_be_named(self, calls: _Calls) -> None:
        _ = invoke(build_app(), ["init", "--database", "rig-2"])

        assert calls.made[1] == ("create_database", "rig-2", None)

    def test_it_defaults_to_the_configured_database(
        self, calls: _Calls, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("INFLUXDB_DATABASE", "from-env")

        _ = invoke(build_app(), ["init"])

        assert calls.made[1] == ("create_database", "from-env", None)

    @pytest.mark.usefixtures("calls")
    def test_a_second_run_changes_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Both server calls refuse, and the command says so calmly."""
        monkeypatch.setattr(admin, "create_admin_token", _already_initialised)
        monkeypatch.setattr(admin, "create_database", _already_exists)
        monkeypatch.setenv("INFLUXDB3_AUTH_TOKEN", "apiv3_existing")

        result = invoke(build_app(), ["init"])

        assert result.exit_code == 0
        assert "already exists" in result.output
        # Nothing was written: the token in hand is the one that works.
        assert not (tmp_path / env.ENV_FILE).exists()

    def test_a_second_run_can_still_change_the_retention(
        self, calls: _Calls, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Creation is refused; retention is a property of the database."""
        monkeypatch.setattr(admin, "create_admin_token", _already_initialised)
        monkeypatch.setattr(admin, "create_database", _already_exists)
        monkeypatch.setenv("INFLUXDB3_AUTH_TOKEN", "apiv3_existing")

        _ = invoke(build_app(), ["init", "--retention", "90d"])

        assert ("set_retention", "lab", "90d") in calls.made

    def test_an_initialised_instance_with_no_token_here_is_refused(
        self, calls: _Calls, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one case that cannot be repaired by trying again.

        An admin token is shown once and the server keeps no copy it will
        hand back, so the command has to say where one can come from
        rather than appear to do something.
        """
        monkeypatch.setattr(admin, "create_admin_token", _already_initialised)

        result = invoke(build_app(), ["init"])

        assert result.exit_code == 2
        assert "no token is configured here" in result.output
        assert "regenerate" in result.output
        assert "create_database" not in calls.names

    def test_a_server_that_is_not_there_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(_host: str) -> str | None:
            raise admin.AdminError("cannot reach http://influx:8181: refused")

        monkeypatch.setattr(admin, "create_admin_token", refuse)

        result = invoke(build_app(), ["init"])

        assert result.exit_code == 2
        assert "cannot reach" in result.output


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token in the environment, as `labmon init` would have left."""
    monkeypatch.setenv("INFLUXDB3_AUTH_TOKEN", "apiv3_configured")


@pytest.mark.usefixtures("token")
class TestResetDatabase:
    """The destructive one, and what stands between it and a mistake."""

    def test_confirmed_by_name_it_drops_and_recreates(self, calls: _Calls) -> None:
        result = invoke(build_app(), ["reset-database", "--yes"])

        assert result.exit_code == 0
        assert calls.names == [
            "database_exists",
            # Read before the delete, because afterwards there is nothing
            # left to read it from.
            "read_retention",
            "delete_database",
            "create_database",
        ]
        assert calls.made[3] == ("create_database", "lab", "30d")

    def test_it_is_soft_unless_asked_otherwise(self, calls: _Calls) -> None:
        _ = invoke(build_app(), ["reset-database", "--yes"])

        assert calls.made[2] == ("delete_database", "lab", False)

    def test_hard_reclaims_the_space(self, calls: _Calls) -> None:
        _ = invoke(build_app(), ["reset-database", "--yes", "--hard"])

        assert calls.made[2] == ("delete_database", "lab", True)

    def test_the_prompt_wants_the_database_name(self, calls: _Calls) -> None:
        """A y/n would be answered by reflex; a name has to be read."""
        result = invoke(build_app(), ["reset-database"], stdin="lab\n")

        assert result.exit_code == 0
        assert "delete_database" in calls.names

    def test_a_wrong_name_deletes_nothing(self, calls: _Calls) -> None:
        """The mistake worth catching is resetting the wrong database."""
        result = invoke(build_app(), ["reset-database"], stdin="lba\n")

        assert result.exit_code == 2
        assert "nothing was deleted" in result.output
        assert "delete_database" not in calls.names

    def test_a_database_that_is_not_there_is_not_created(
        self, calls: _Calls, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`reset` creating a database would be a surprising reading of it."""
        monkeypatch.setattr(admin, "database_exists", _absent)

        result = invoke(build_app(), ["reset-database", "--database", "typo", "--yes"])

        assert result.exit_code == 2
        assert "no such database" in result.output
        assert "delete_database" not in calls.names

    def test_without_a_token_it_points_at_init(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing can be reset before the instance has been set up."""
        monkeypatch.delenv("INFLUXDB3_AUTH_TOKEN", raising=False)

        result = invoke(build_app(), ["reset-database", "--yes"])

        assert result.exit_code == 2
        assert "labmon init" in result.output


class TestEnvWrite:
    """Editing `.env` in place, which Compose reads as well."""

    def test_an_existing_assignment_is_replaced_where_it_stands(
        self, tmp_path: Path
    ) -> None:
        """So the comment above it still describes the line beneath."""
        path = tmp_path / env.ENV_FILE
        _ = path.write_text(
            "# The token InfluxDB issued.\nINFLUXDB3_AUTH_TOKEN=\nOTHER=keep\n",
            encoding="utf-8",
        )

        _ = env.write("INFLUXDB3_AUTH_TOKEN", "apiv3_new", tmp_path)

        assert path.read_text(encoding="utf-8") == (
            "# The token InfluxDB issued.\nINFLUXDB3_AUTH_TOKEN=apiv3_new\nOTHER=keep\n"
        )

    def test_a_missing_assignment_is_appended(self, tmp_path: Path) -> None:
        path = tmp_path / env.ENV_FILE
        _ = path.write_text("OTHER=keep\n", encoding="utf-8")

        _ = env.write("INFLUXDB3_AUTH_TOKEN", "apiv3_new", tmp_path)

        assert path.read_text(encoding="utf-8") == (
            "OTHER=keep\nINFLUXDB3_AUTH_TOKEN=apiv3_new\n"
        )

    def test_a_file_that_does_not_exist_is_created(self, tmp_path: Path) -> None:
        written = env.write("INFLUXDB3_AUTH_TOKEN", "apiv3_new", tmp_path)

        assert written == tmp_path / env.ENV_FILE
        assert written.read_text(encoding="utf-8") == "INFLUXDB3_AUTH_TOKEN=apiv3_new\n"

    def test_the_value_is_never_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The only setting labmon writes here is a credential."""
        with caplog.at_level(logging.INFO):
            _ = env.write("INFLUXDB3_AUTH_TOKEN", "apiv3_secret", tmp_path)

        [record] = caplog.records
        rendered = caplog.text + repr(sorted(record.__dict__.items(), key=str))
        assert "apiv3_secret" not in rendered
        # The name is logged, because knowing which setting changed is the
        # point of the line.
        assert _field(record, "setting") == "INFLUXDB3_AUTH_TOKEN"

    def test_a_name_that_is_a_prefix_of_another_is_not_confused(
        self, tmp_path: Path
    ) -> None:
        """`INFLUXDB_DATABASE=` must not be matched by `INFLUXDB_DATA`."""
        path = tmp_path / env.ENV_FILE
        _ = path.write_text("INFLUXDB_DATABASE=lab\n", encoding="utf-8")

        _ = env.write("INFLUXDB_DATA", "x", tmp_path)

        assert "INFLUXDB_DATABASE=lab" in path.read_text(encoding="utf-8")
