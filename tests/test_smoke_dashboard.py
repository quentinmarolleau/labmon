"""The check that every dashboard panel still returns rows.

`tests/test_dashboard.py` checks each dashboard's shape. This file
checks the script that checks it *works* — the one that interpolates the
dashboard variables the way Grafana's frontend would and sends every
target through Grafana's own query endpoint.

That script runs against a live stack in CI, so its happy path is well
covered and its failure paths are not: a run where a panel returns
nothing, or where the credentials are refused, is not a run CI can be
asked to have. Those are what is checked here. The interpolation and
loading halves are exercised against the real dashboards in
`grafana/dashboards/`, since a fixture of a dashboard would drift from
the file that ships.
"""

import io
import json
import ssl
import sys
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Self, cast

import pytest

from tests.loader import (
    SMOKE_DASHBOARD,
    Query,
    path_to,
    run_as_main,
    smoke_dashboard,
)

smoke = smoke_dashboard()

# Bound once rather than suppressed at each call site, the way
# tests/test_influx.py takes `_setting`.
Rejected = smoke._Rejected  # pyright: ignore[reportPrivateUsage]
make_query = smoke._Query  # pyright: ignore[reportPrivateUsage]
LOG_LINE_LIMIT = smoke._LOG_LINE_LIMIT  # pyright: ignore[reportPrivateUsage]
mapping = smoke._mapping  # pyright: ignore[reportPrivateUsage]
mappings = smoke._mappings  # pyright: ignore[reportPrivateUsage]
values_of = smoke._values_of  # pyright: ignore[reportPrivateUsage]
interpolate = smoke._interpolate  # pyright: ignore[reportPrivateUsage]
child = smoke._child  # pyright: ignore[reportPrivateUsage]
first = smoke._first  # pyright: ignore[reportPrivateUsage]
row_count = smoke._row_count  # pyright: ignore[reportPrivateUsage]
payload_for = smoke._payload  # pyright: ignore[reportPrivateUsage]
load_queries = smoke._load_queries  # pyright: ignore[reportPrivateUsage]
select_dashboards = smoke._selected  # pyright: ignore[reportPrivateUsage]
tls_context = smoke._tls_context  # pyright: ignore[reportPrivateUsage]
post = smoke._post  # pyright: ignore[reportPrivateUsage]
report = smoke._report  # pyright: ignore[reportPrivateUsage]

_DASHBOARDS = path_to("grafana/dashboards")


class _Clock:
    """A monotonic clock the test advances, so no test waits five seconds."""

    def __init__(self) -> None:
        self.now: float = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _response(payload: object) -> object:
    """Something `urlopen` could have returned: a context manager that reads."""
    body = json.dumps(payload).encode()

    class _Response:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return body

    return _Response()


def _http_error(code: int, body: bytes = b"column not found") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:3000", code, "nope", Message(), io.BytesIO(body)
    )


def _query(label: str, *, may_be_empty: bool = False) -> Query:
    return make_query(
        label=label,
        payload={"refId": "A", "rawSql": "SELECT 1"},
        may_be_empty=may_be_empty,
    )


def _frames(*column_lengths: int) -> dict[str, object]:
    """A Grafana result holding one frame per length given."""
    return {
        "frames": [
            {"data": {"values": [list(range(length))]}} for length in column_lengths
        ]
    }


class TestNarrowing:
    """The two JSON helpers, which exist so the rest can stay typed."""

    def test_a_mapping_passes_through(self) -> None:
        assert mapping({"a": 1}) == {"a": 1}

    def test_a_non_mapping_names_what_it_got(self) -> None:
        with pytest.raises(TypeError, match="expected an object, got int"):
            _ = mapping(3)

    def test_a_list_of_mappings_passes_through(self) -> None:
        assert mappings([{"a": 1}]) == [{"a": 1}]

    def test_a_non_list_names_what_it_got(self) -> None:
        with pytest.raises(TypeError, match="expected a list, got str"):
            _ = mappings("nope")


class TestVariableValues:
    """What the frontend would put in place of each dashboard variable."""

    def test_a_custom_variable_yields_every_value(self) -> None:
        """All of them, because `${room:singlequote}` reaches `IN (...)`.

        Taking the first would quietly narrow a multi-select to one room
        and the panel would still draw.
        """
        variable: dict[str, object] = {
            "type": "custom",
            "query": "room-1, room-2 ,, room-3",
        }
        assert values_of(variable) == ["room-1", "room-2", "room-3"]

    def test_a_custom_variable_with_no_string_query_yields_nothing(self) -> None:
        variable: dict[str, object] = {"type": "custom", "query": None}
        assert values_of(variable) == []

    def test_a_single_valued_variable_uses_its_current_value(self) -> None:
        variable: dict[str, object] = {
            "type": "query",
            "current": {"value": "influxdb"},
        }
        assert values_of(variable) == ["influxdb"]

    def test_a_multi_valued_variable_uses_all_of_them(self) -> None:
        variable: dict[str, object] = {
            "type": "query",
            "current": {"value": ["a", "b"]},
        }
        assert values_of(variable) == ["a", "b"]

    def test_all_selected_interpolates_the_declared_all_value(self) -> None:
        """What a browser sends on load, which resolves it without a round trip."""
        variable: dict[str, object] = {
            "type": "query",
            "current": {"value": ["$__all"]},
            "allValue": ".+",
        }
        assert values_of(variable) == [".+"]

    def test_all_selected_with_no_all_value_cannot_be_resolved(self) -> None:
        """Guessing would send a query that differs from the shipped panel's."""
        variable: dict[str, object] = {
            "name": "container",
            "type": "query",
            "current": {"value": "$__all"},
        }
        with pytest.raises(SystemExit, match="declares no allValue"):
            _ = values_of(variable)


# Two variables of the shape the real dashboards carry: one multi-valued
# selector reaching an `IN (...)`, one single-valued.
_VARIABLES: dict[str, list[str]] = {
    "room": ["room-1", "room-2"],
    "channel": ["cryo-diode"],
}


class TestInterpolation:
    """Substitution, which has to match the frontend or the check is fiction."""

    def test_singlequote_quotes_and_joins_every_value(self) -> None:
        rendered = interpolate("WHERE room IN (${room:singlequote})", _VARIABLES)
        assert rendered == "WHERE room IN ('room-1','room-2')"

    def test_another_format_joins_without_quoting(self) -> None:
        rendered = interpolate("${room:csv}", _VARIABLES)
        assert rendered == "room-1,room-2"

    def test_a_bare_variable_takes_the_first_value(self) -> None:
        assert interpolate("$channel and $room", _VARIABLES) == (
            "cryo-diode and room-1"
        )

    def test_a_braced_variable_takes_the_first_value(self) -> None:
        assert interpolate("${channel}", _VARIABLES) == "cryo-diode"

    def test_grafana_macros_are_left_for_the_backend(self) -> None:
        """`$__timeFrom()` and `$__auto` are expanded server-side.

        Substituting them here would send the backend something it has
        no macro for, and the panel would fail for a reason invented by
        this script rather than by the dashboard.
        """
        sql = "WHERE time >= $__timeFrom() AND x = $__auto"
        assert interpolate(sql, _VARIABLES) == sql

    def test_an_unknown_variable_is_left_alone(self) -> None:
        """Both spellings, so an unresolved name reaches the backend intact."""
        assert interpolate("$nope ${nope} ${nope:singlequote}", {}) == (
            "$nope ${nope} ${nope:singlequote}"
        )

    def test_a_variable_with_no_values_is_left_alone(self) -> None:
        assert interpolate("$empty", {"empty": []}) == "$empty"


class TestResultWalking:
    """Grafana's response is nested five levels deep and typed as `object`."""

    def test_a_child_of_a_mapping_is_returned(self) -> None:
        assert child({"a": 1}, "a") == 1

    def test_a_child_of_anything_else_is_none(self) -> None:
        assert child(["a"], "a") is None

    def test_the_first_item_of_a_list(self) -> None:
        assert first([1, 2]) == 1

    @pytest.mark.parametrize("value", [[], "no", None], ids=["empty", "str", "none"])
    def test_no_first_item(self, value: object) -> None:
        assert first(value) is None

    def test_rows_are_counted_across_every_frame(self) -> None:
        """One frame per stream for Loki, and the first can be empty.

        Counting only the first frame would read a working multi-stream
        query as a dead one.
        """
        assert row_count(_frames(0, 3, 2)) == 5

    def test_a_result_with_no_frames_counts_nothing(self) -> None:
        assert row_count({"frames": "not a list"}) == 0

    def test_a_frame_with_no_columns_counts_nothing(self) -> None:
        empty: dict[str, object] = {"frames": [{"data": {"values": []}}]}
        assert row_count(empty) == 0


class TestPayload:
    """Each backend wants its query under a different key, with different extras."""

    def test_an_influxdb_target_carries_sql_and_a_format(self) -> None:
        target: dict[str, object] = {
            "refId": "A",
            "datasource": {"type": "influxdb"},
            "rawSql": "SELECT 1",
        }
        payload = payload_for("influxdb", target, {})

        assert payload["rawSql"] == "SELECT 1"
        assert payload["format"] == "time_series"
        assert "maxLines" not in payload

    def test_a_loki_target_carries_an_expression_and_a_line_limit(self) -> None:
        """`queryType` decides range against instant; sending neither is empty."""
        target: dict[str, object] = {
            "refId": "B",
            "datasource": {"type": "loki"},
            "expr": '{container=~".+"}',
            "queryType": "instant",
        }
        payload = payload_for("loki", target, {})

        assert payload["expr"] == '{container=~".+"}'
        assert payload["queryType"] == "instant"
        assert payload["maxLines"] == LOG_LINE_LIMIT
        assert "format" not in payload

    def test_a_loki_target_defaults_to_a_range_query(self) -> None:
        target: dict[str, object] = {
            "refId": "B",
            "datasource": {"type": "loki"},
            "expr": '{a="b"}',
        }
        payload = payload_for("loki", target, {})
        assert payload["queryType"] == "range"


class TestLoadingTheRealDashboards:
    """Against the files that ship, so a fixture cannot drift from them."""

    @pytest.mark.parametrize(
        "stem", [path.stem for path in sorted(_DASHBOARDS.glob("*.json"))]
    )
    def test_every_panel_target_becomes_a_query(self, stem: str) -> None:
        path = _DASHBOARDS / f"{stem}.json"
        parsed = cast(dict[str, object], json.loads(path.read_text("utf-8")))

        queries = load_queries(path, parsed)

        assert queries, f"{stem} produced no queries at all"
        for query in queries:
            assert query.label.startswith(f"{stem}/")
            field = "expr" if "expr" in query.payload else "rawSql"
            rendered = str(query.payload[field])
            # A variable left unsubstituted is the failure this script
            # exists to avoid making itself: it would reach the backend
            # as a literal `$room` and fail for the wrong reason.
            assert "${" not in rendered
            for token in rendered.split("$")[1:]:
                assert token.startswith("__"), f"{query.label} left ${token[:12]}"

    def test_an_unknown_datasource_type_is_an_error_not_a_skip(self) -> None:
        """Adding a backend must not silently switch a dashboard's check off."""
        dashboard: dict[str, object] = {
            "templating": {"list": []},
            "panels": [
                {
                    "title": "Prometheus panel",
                    "targets": [{"refId": "A", "datasource": {"type": "prometheus"}}],
                }
            ],
        }
        with pytest.raises(SystemExit, match="whose query field this script does not"):
            _ = load_queries(Path("made-up.json"), dashboard)


class TestSelection:
    """`--dashboard`, and the rule that an unknown name is an error."""

    def test_no_argument_takes_every_dashboard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(smoke, "_DASHBOARD_DIR", _DASHBOARDS)
        selected = select_dashboards(None)
        assert [path.stem for path in selected] == ["lab-overview", "logs"]

    def test_a_named_subset_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(smoke, "_DASHBOARD_DIR", _DASHBOARDS)
        selected = select_dashboards(["logs"])
        assert [path.stem for path in selected] == ["logs"]

    def test_an_unknown_name_lists_what_there_is(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A typo would otherwise report success having checked nothing."""
        monkeypatch.setattr(smoke, "_DASHBOARD_DIR", _DASHBOARDS)
        with pytest.raises(SystemExit, match="no dashboard named lgos"):
            _ = select_dashboards(["lgos"])

    def test_an_empty_directory_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(smoke, "_DASHBOARD_DIR", tmp_path)
        with pytest.raises(SystemExit, match="no dashboards found"):
            _ = select_dashboards(None)


class TestTlsContext:
    """`--cacert`, which is what makes the tls route reachable at all."""

    def test_no_cacert_means_the_system_store(self) -> None:
        assert tls_context(None) is None

    def test_a_cacert_that_is_not_a_file_stops_the_run(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="which is not a file"):
            _ = tls_context(str(tmp_path / "absent.crt"))

    def test_a_real_ca_is_loaded(self, tmp_path: Path) -> None:
        bundle = Path(ssl.get_default_verify_paths().cafile)
        if not bundle.is_file():  # pragma: no cover - host without a CA bundle
            pytest.skip("no system CA bundle to borrow")
        certificate = tmp_path / "ca.crt"
        _ = certificate.write_bytes(bundle.read_bytes())

        context = tls_context(str(certificate))

        assert isinstance(context, ssl.SSLContext)
        assert context.get_ca_certs(), "the file was accepted but loaded nothing"


class TestPost:
    """The one network call, and the credentials it carries."""

    def test_the_request_is_authenticated_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[urllib.request.Request] = []

        def opened(request: urllib.request.Request, **_kwargs: object) -> object:
            seen.append(request)
            return _response({"results": {}})

        monkeypatch.setattr("urllib.request.urlopen", opened)

        answer = post({"queries": []}, "hunter2", "http://x/api", None)

        assert answer == {"results": {}}
        request = seen[0]
        assert request.full_url == "http://x/api"
        assert request.get_header("Content-type") == "application/json"
        authorization = request.get_header("Authorization")
        assert authorization is not None and authorization.startswith("Basic ")
        assert json.loads(cast(bytes, request.data)) == {"queries": []}


class TestAttempt:
    """One pass over every query, and which failures are worth retrying."""

    @staticmethod
    def _run(
        monkeypatch: pytest.MonkeyPatch,
        answer: object,
        queries: list[Query] | None = None,
    ) -> list[tuple[str, str]]:
        def posted(*_args: object, **_kwargs: object) -> object:
            if isinstance(answer, BaseException):
                raise answer
            return answer

        monkeypatch.setattr(smoke, "_post", posted)
        return smoke._attempt(  # pyright: ignore[reportPrivateUsage]
            queries or [_query("d/Panel")],
            {"from": "now-15m", "to": "now"},
            "pw",
            "u",
            None,
        )

    def test_a_query_returning_rows_is_not_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        answer = {"results": {"A": _frames(3)}}
        assert self._run(monkeypatch, answer) == []

    def test_a_query_returning_nothing_is_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The case nothing else catches: valid LogQL that matches nothing."""
        answer = {"results": {"A": _frames(0)}}
        assert self._run(monkeypatch, answer) == [
            ("d/Panel", "query succeeded but returned no rows")
        ]

    def test_a_panel_allowed_to_be_empty_is_left_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A run where nothing logged a warning is a good run."""
        answer = {"results": {"A": _frames(0)}}
        queries = [_query("logs/Warnings", may_be_empty=True)]
        assert self._run(monkeypatch, answer, queries=queries) == []

    def test_a_datasource_error_is_reported_verbatim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        answer = {"results": {"A": {"error": "table not found: temperature"}}}
        failures = self._run(monkeypatch, answer)
        assert failures == [("d/Panel", "table not found: temperature")]

    def test_an_http_error_carries_the_body_not_just_the_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bad column and a bad token are both "400 Bad Request"."""
        failures = self._run(monkeypatch, _http_error(400))
        assert "column not found" in failures[0][1]

    @pytest.mark.parametrize("code", [401, 403])
    def test_refused_credentials_stop_the_retry_loop(
        self, monkeypatch: pytest.MonkeyPatch, code: int
    ) -> None:
        with pytest.raises(Rejected):
            _ = self._run(monkeypatch, _http_error(code))

    def test_an_unreachable_grafana_is_a_retryable_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        failures = self._run(monkeypatch, urllib.error.URLError("refused"))
        assert "refused" in failures[0][1]


class TestReport:
    """Grouping, so one broken panel is not buried under twenty copies."""

    def test_failures_are_grouped_by_reason(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        queries = [_query("a/One"), _query("a/Two"), _query("b/Three")]
        failures = [("a/One", "same"), ("a/Two", "same"), ("b/Three", "other")]

        report(failures, queries, 60.0, 3)

        errors = capsys.readouterr().err
        assert "3 of 3 dashboard queries still failing after 60s (3 attempts)" in errors
        assert "  - a/One, a/Two: same" in errors
        assert "  - b/Three: other" in errors

    def test_a_whole_dashboard_down_gets_the_profile_hint(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Every query on one dashboard failing usually means its profile is off."""
        queries = [_query("logs/One"), _query("lab/Two")]
        report([("logs/One", "no such datasource")], queries, 60.0, 1)

        assert "the profile behind" in capsys.readouterr().err

    def test_one_broken_panel_among_working_ones_gets_no_hint(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """It would point at the wrong thing, which is worse than silence."""
        queries = [_query("logs/One"), _query("logs/Two")]
        report([("logs/One", "no rows")], queries, 60.0, 1)

        assert "the profile behind" not in capsys.readouterr().err


class TestMain:
    """The loop, run against the real dashboards with only the network faked."""

    @staticmethod
    def _run(
        monkeypatch: pytest.MonkeyPatch,
        attempts: list[object],
        argv: list[str] | None = None,
    ) -> tuple[int, _Clock]:
        clock = _Clock()
        monkeypatch.setattr(smoke, "time", clock)
        monkeypatch.setattr(smoke, "_DASHBOARD_DIR", _DASHBOARDS)
        monkeypatch.setattr(sys, "argv", ["smoke_dashboard.py", *(argv or [])])

        remaining = list(attempts)

        def attempt(*_args: object, **_kwargs: object) -> object:
            answer = remaining.pop(0) if len(remaining) > 1 else remaining[0]
            if isinstance(answer, BaseException):
                raise answer
            return answer

        monkeypatch.setattr(smoke, "_attempt", attempt)
        return smoke.main(), clock

    def test_every_query_returning_rows_passes(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        status, clock = self._run(monkeypatch, [[]])

        output = capsys.readouterr().out
        assert status == 0
        assert clock.slept == []
        assert "across 2 dashboards returned rows" in output
        assert "not counting 3 allowed to be empty" in output

    def test_a_single_dashboard_reads_singular(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        status, _ = self._run(monkeypatch, [[]], argv=["--dashboard", "lab-overview"])

        output = capsys.readouterr().out
        assert status == 0
        assert "across 1 dashboard returned rows" in output
        # lab-overview has no panel on the may-be-empty list, so the
        # note is absent rather than reading "not counting 0".
        assert "not counting" not in output

    def test_a_slow_stack_is_waited_for(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The sensors need a moment to fill the dashboard's window."""
        status, clock = self._run(monkeypatch, [[("logs/Rate", "no rows")], []])

        assert status == 0
        assert clock.slept == [5]
        assert "1 not ready yet, retrying" in capsys.readouterr().out

    def test_a_query_that_never_recovers_fails_with_a_report(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        status, clock = self._run(
            monkeypatch, [[("logs/Rate", "no rows")]], argv=["--timeout", "10"]
        )

        assert status == 1
        assert clock.slept == [5, 5]
        assert "1 of" in capsys.readouterr().err

    def test_refused_credentials_end_the_run(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        status, clock = self._run(monkeypatch, [Rejected("401 on logs/Rate")])

        assert status == 1
        assert clock.slept == []
        assert "Grafana rejected the credentials" in capsys.readouterr().err


def test_running_it_as_a_script_exits_with_its_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard block, which is how CI actually invokes this file."""
    monkeypatch.setattr(sys, "argv", ["smoke_dashboard.py", "--dashboard", "logs"])

    def opened(request: urllib.request.Request, **_kwargs: object) -> object:
        """Answer whatever refId the script asked about, with one row."""
        sent = cast(dict[str, object], json.loads(cast(bytes, request.data)))
        asked = cast(list[dict[str, object]], sent["queries"])
        return _response({"results": {str(asked[0]["refId"]): _frames(1)}})

    monkeypatch.setattr("urllib.request.urlopen", opened)

    with pytest.raises(SystemExit) as exited:
        _ = run_as_main(SMOKE_DASHBOARD)

    assert exited.value.code == 0
