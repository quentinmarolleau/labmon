"""The check that log collection is actually collecting.

`scripts/smoke_logs.py` is the only thing that would notice the `logs`
profile going quiet: Alloy failing to parse its config, Loki refusing
pushes and a container buffering its output all look identical from
outside, and all of them look like a quiet lab. It runs against a live
stack in CI, which proves it works but cannot reach its failure paths —
a run where the credentials are refused, or where one container of
twelve is missing, is not a run anybody wants CI to have.

Those are what is checked here: the reporting and the give-up rules,
against a stack that is fabricated rather than started.
"""

import io
import ssl
import subprocess
import sys
import urllib.error
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from typing import Self, cast

import certifi
import pytest

from tests.loader import SMOKE_LOGS, run_as_main, smoke_logs

smoke = smoke_logs()

# Bound once rather than suppressed at each call site, the way
# tests/test_influx.py takes `_setting`.
Rejected = smoke._Rejected  # pyright: ignore[reportPrivateUsage]
running_containers = smoke._running_containers  # pyright: ignore[reportPrivateUsage]
tls_context = smoke._tls_context  # pyright: ignore[reportPrivateUsage]
collected_containers = smoke._collected_containers  # pyright: ignore[reportPrivateUsage]


class _Clock:
    """A monotonic clock the test advances, so no test waits five seconds.

    The retry loop sleeps between attempts and gives up on a deadline;
    both are read through this, so a run that would take three minutes of
    wall clock takes none.
    """

    def __init__(self) -> None:
        self.now: float = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _response(payload: bytes) -> object:
    """Something `urlopen` could have returned: a context manager that reads."""

    class _Response:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return payload

    return _Response()


def _completing(stdout: str) -> Callable[..., subprocess.CompletedProcess[str]]:
    """A `subprocess.run` that reports what Compose would have said."""

    def ran(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stdout, stderr=""
        )

    return ran


def _answering(payload: bytes) -> Callable[..., object]:
    """A `urlopen` that always hands back the same body."""

    def opened(*_args: object, **_kwargs: object) -> object:
        return _response(payload)

    return opened


def _http_error(code: int, body: bytes = b"denied") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:3000", code, "nope", Message(), io.BytesIO(body)
    )


class TestRunningContainers:
    """What Compose says is up, which is the list everything is judged against."""

    def test_names_are_read_from_compose(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Blank lines are dropped; the rest come back as a set."""
        monkeypatch.setattr(subprocess, "run", _completing("influxdb\ngrafana\n\n"))
        assert running_containers() == {"influxdb", "grafana"}

    def test_missing_docker_is_not_worth_retrying(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def absent(*_args: object, **_kwargs: object) -> object:
            raise FileNotFoundError

        monkeypatch.setattr(subprocess, "run", absent)
        with pytest.raises(Rejected, match="docker is not on PATH"):
            _ = running_containers()

    def test_a_failed_compose_call_carries_its_stderr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Usually "run from outside the repo", and the message should say so."""

        def failed(*_args: object, **_kwargs: object) -> object:
            raise subprocess.CalledProcessError(
                1, "docker", stderr="no configuration file provided\n"
            )

        monkeypatch.setattr(subprocess, "run", failed)
        with pytest.raises(Rejected, match="no configuration file provided"):
            _ = running_containers()


class TestTlsContext:
    """`--cacert`, which is what makes the tls route reachable at all."""

    def test_no_cacert_means_the_system_store(self) -> None:
        assert tls_context(None) is None

    def test_a_cacert_that_is_not_a_file_stops_the_run(self, tmp_path: Path) -> None:
        """Named and quoted, because the usual cause is a typo in a path."""
        missing = str(tmp_path / "absent.crt")
        with pytest.raises(SystemExit, match="which is not a file"):
            _ = tls_context(missing)

    def test_a_real_ca_is_loaded(self, tmp_path: Path) -> None:
        """The exported CA has to be trusted, not merely found.

        certifi's bundle is borrowed rather than a certificate minted,
        which would pull in `cryptography` for one assertion. Not
        `ssl.get_default_verify_paths()`: typeshed declares its `cafile`
        a `str`, and on a CI runner it is None.
        """
        certificate = tmp_path / "ca.crt"
        _ = certificate.write_bytes(Path(certifi.where()).read_bytes())

        context = tls_context(str(certificate))

        assert isinstance(context, ssl.SSLContext)
        assert context.get_ca_certs(), "the file was accepted but loaded nothing"


class TestCollectedContainers:
    """What Loki answers, and which answers are worth retrying."""

    @staticmethod
    def _patch(monkeypatch: pytest.MonkeyPatch, result: object) -> None:
        def opened(*_args: object, **_kwargs: object) -> object:
            if isinstance(result, BaseException):
                raise result
            return result

        monkeypatch.setattr("urllib.request.urlopen", opened)

    def test_label_values_become_the_collected_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch(monkeypatch, _response(b'{"data": ["influxdb", "grafana"]}'))
        collected = collected_containers("pw", "http://x", None)
        assert collected == {"influxdb", "grafana"}

    def test_a_body_with_no_data_list_is_empty_rather_than_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Loki answers this while it is still starting, and it will pass."""
        self._patch(monkeypatch, _response(b'{"status": "success"}'))
        assert collected_containers("pw", "http://x", None) == set()

    @pytest.mark.parametrize("code", [401, 403])
    def test_refused_credentials_are_terminal(
        self, monkeypatch: pytest.MonkeyPatch, code: int
    ) -> None:
        """Retrying would lock the admin out after five attempts.

        Grafana blocks an account on consecutive failures, so a retry
        loop against a wrong password does more damage than the failure
        it is trying to ride out.
        """
        self._patch(monkeypatch, _http_error(code))
        with pytest.raises(Rejected):
            _ = collected_containers("pw", "http://x", None)

    def test_other_http_errors_stay_retryable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 502 while Grafana starts is exactly what the loop is for."""
        self._patch(monkeypatch, _http_error(502))
        with pytest.raises(urllib.error.HTTPError):
            _ = collected_containers("pw", "http://x", None)


class TestMain:
    """The loop: what it waits for, what it gives up on, what it prints."""

    @staticmethod
    def _run(
        monkeypatch: pytest.MonkeyPatch,
        *,
        running: object,
        collected: list[object],
        argv: list[str] | None = None,
    ) -> tuple[int, _Clock]:
        clock = _Clock()
        monkeypatch.setattr(smoke, "time", clock)
        monkeypatch.setattr(sys, "argv", ["smoke_logs.py", *(argv or [])])

        def containers() -> set[str]:
            if isinstance(running, BaseException):
                raise running
            return cast(set[str], running)

        answers = list(collected)

        def collect(*_args: object, **_kwargs: object) -> set[str]:
            answer = answers.pop(0) if len(answers) > 1 else answers[0]
            if isinstance(answer, BaseException):
                raise answer
            return cast(set[str], answer)

        monkeypatch.setattr(smoke, "_running_containers", containers)
        monkeypatch.setattr(smoke, "_collected_containers", collect)
        return smoke.main(), clock

    def test_every_container_collected_passes_first_time(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        status, clock = self._run(
            monkeypatch,
            running={"influxdb", "grafana"},
            collected=[{"influxdb", "grafana"}],
        )

        assert status == 0
        assert clock.slept == []
        assert "all 2 running containers have logs in Loki" in capsys.readouterr().out

    def test_loki_is_excused_and_said_to_be(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Loki runs at log_level=warn so that it has nothing to say.

        Naming it in the success line is what stops the exemption being
        invisible: a reader counting containers would otherwise find one
        missing and no explanation.
        """
        status, _ = self._run(
            monkeypatch,
            running={"influxdb", "grafana", "loki"},
            collected=[{"influxdb", "grafana"}],
        )

        output = capsys.readouterr().out
        assert status == 0
        assert "all 2 running containers" in output
        assert "not counting loki" in output

    def test_a_container_arriving_late_is_waited_for(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Alloy discovers containers a moment after they start."""
        status, clock = self._run(
            monkeypatch,
            running={"influxdb", "grafana"},
            collected=[{"influxdb"}, {"influxdb", "grafana"}],
        )

        assert status == 0
        assert clock.slept == [5]
        assert "1 not collected yet, retrying" in capsys.readouterr().out

    def test_an_unreachable_loki_is_retried_not_failed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Loki has no healthcheck, so early attempts land before it listens."""
        status, _ = self._run(
            monkeypatch,
            running={"influxdb"},
            collected=[urllib.error.URLError("connection refused"), {"influxdb"}],
        )

        assert status == 0
        assert "not ready yet" in capsys.readouterr().out

    def test_a_container_that_never_arrives_fails_with_its_name(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The single missing container is the failure worth reporting.

        A non-empty check would pass here, which is the whole reason this
        compares against the running set.
        """
        status, clock = self._run(
            monkeypatch,
            running={"influxdb", "mock-cryo-4k"},
            collected=[{"influxdb"}],
            argv=["--timeout", "12"],
        )

        assert status == 1
        assert clock.slept == [5, 5, 5]
        errors = capsys.readouterr().err
        assert "1 of 2 containers have no logs in Loki after 12s" in errors
        assert "  - mock-cryo-4k" in errors
        assert "usually buffering rather than a collection fault" in errors

    def test_a_stack_that_is_not_up_says_so(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        status, _ = self._run(monkeypatch, running=set[str](), collected=[set[str]()])

        assert status == 1
        assert "no running containers" in capsys.readouterr().err

    def test_compose_being_unusable_is_reported_not_retried(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        status, _ = self._run(
            monkeypatch,
            running=Rejected("docker is not on PATH"),
            collected=[set()],
        )

        assert status == 1
        assert "cannot list the stack's containers" in capsys.readouterr().err

    def test_refused_credentials_end_the_run(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        status, clock = self._run(
            monkeypatch,
            running={"influxdb"},
            collected=[Rejected("401")],
        )

        assert status == 1
        assert clock.slept == []
        assert "Grafana rejected the credentials" in capsys.readouterr().err


def test_running_it_as_a_script_exits_with_its_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard block, which is how CI actually invokes this file."""
    monkeypatch.setattr(sys, "argv", ["smoke_logs.py"])
    monkeypatch.setattr(subprocess, "run", _completing("influxdb\n"))
    monkeypatch.setattr("urllib.request.urlopen", _answering(b'{"data": ["influxdb"]}'))

    with pytest.raises(SystemExit) as exited:
        _ = run_as_main(SMOKE_LOGS)

    assert exited.value.code == 0
