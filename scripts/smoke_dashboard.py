"""Run every dashboard's queries against a live stack and count the rows.

The unit tests in `tests/test_dashboard.py` check each dashboard's shape.
This checks that it *works*: it takes every panel target, substitutes the
dashboard variables the way Grafana's frontend would, and sends it through
Grafana's own query endpoint — same datasource, same token, same connection
a browser would use.

That covers the gap nothing else does. A query can be structurally perfect
and still name a column InfluxDB never created, or reference a table only a
mock sensor writes; both render as an empty panel and log nothing. Here they
fail out loud.

Both backends are covered, which matters more for Loki than for InfluxDB. A
column that does not exist is at least a SQL error, but LogQL answers a
renamed logfmt field, a `msg=` that matches nothing and a mistyped label
with an empty result and no error at all — so counting rows is the only
thing that can tell a working query from one that has stopped meaning
anything.

Every dashboard in `grafana/dashboards/` is checked, so the profile behind
each one has to be running. `--dashboard` narrows it to a subset.

Stdlib only, so CI can run it without installing the project.

Usage:
    python3 scripts/smoke_dashboard.py [--dashboard NAME] [--timeout SECONDS]
                                      [--password PASSWORD]

The password defaults to $GRAFANA_ADMIN_PASSWORD, then to `admin`. Note that
`.env` is read by Compose, not by your shell, so a password set there has to
be exported or passed explicitly.
"""

import argparse
import base64
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import NamedTuple, cast

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "grafana" / "dashboards"

# The key each datasource type reads its query from. A query written under
# any other name is accepted by the JSON, sent to the backend empty, and
# comes back as an error the panel never shows. `tests/test_dashboard.py`
# holds the same table for its structural checks; a type missing from
# either fails rather than being skipped, so adding a backend cannot
# silently switch the check off for the dashboard that uses it.
_QUERY_FIELD = {"influxdb": "rawSql", "loki": "expr"}

# Panels a healthy stack may leave empty, as (dashboard stem, panel title).
#
# The three named here filter on severity, and a run where nothing logged a
# warning is a good run rather than a broken dashboard. They would also
# pass for the wrong reason: the one error a quiet stack does produce is
# Loki's transient ring-initialisation failure at startup, so requiring
# rows would really be asserting an upstream bug, and the check would begin
# failing the day that bug is fixed.
#
# Anything not listed has to return rows. Defaulting the other way would
# let a panel stop meaning anything without a word, which is the whole
# failure this file exists to catch.
_MAY_BE_EMPTY = frozenset(
    {
        ("logs", "Warnings"),
        ("logs", "Errors"),
        ("logs", "Warnings and errors"),
    }
)

# What Grafana puts in a variable's `current.value` when All is selected.
_ALL_SENTINEL = "$__all"

# A logs panel returns raw lines, and this only needs to know that some
# arrived. Grafana's own default is 1000, which is 1000 log lines per panel
# over the wire for no gain.
_LOG_LINE_LIMIT = 100

# Where Grafana is by default. `--url` points this at the `tls` profile's
# proxy instead (https://127.0.0.1:3443), which is the same Grafana reached
# by the route a viewer actually uses once TLS is on.
_DEFAULT_GRAFANA_URL = "http://127.0.0.1:3000"

# Grafana blocks an account after five consecutive bad passwords, so retrying
# one is worse than useless: three minutes of retries would lock the admin out
# of the UI as well, long after the password was corrected.
_TERMINAL_STATUSES = frozenset({401, 403})

# Grafana's own macros are expanded server-side by the datasource backend, so
# they are left alone — `$__timeFrom()` by InfluxDB, and `$__auto` and
# `$__range` by Loki, which is worth stating because those two read like
# frontend interpolation and are not. Dashboard variables are the other way
# round: the frontend substitutes those before the query is ever sent, which
# is what this has to reproduce.
_PLAIN_VARIABLE = re.compile(r"\$\{(\w+)\}|\$(\w+)\b")
_FORMATTED_VARIABLE = re.compile(r"\$\{(\w+):(\w+)\}")


def _mapping(value: object) -> dict[str, object]:
    """Narrow a parsed-JSON node to a mapping.

    `tests/test_dashboard.py` carries the same pair. Duplicating six lines is
    the price of this script importing nothing outside the stdlib, so CI can
    run it against a live stack without installing the project.
    """
    if not isinstance(value, dict):
        raise TypeError(f"expected an object, got {type(value).__name__}")
    return cast(dict[str, object], value)


def _mappings(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise TypeError(f"expected a list, got {type(value).__name__}")
    return [_mapping(item) for item in cast(list[object], value)]


def _values_of(variable: dict[str, object]) -> list[str]:
    """The values Grafana's frontend would substitute for one variable.

    A custom variable's values are its comma-separated `query`, and all of
    them are used: `${room:singlequote}` reaching `IN (...)` is the case
    that matters, and taking only the first would quietly narrow it. That
    covers a custom variable set to All as well, which Grafana expands to
    exactly the same list.

    A query variable's values come from the datasource, which this script
    does not ask. One shipped with All selected interpolates `allValue`
    instead, and that is what a browser sends on load — enough to resolve
    the Logs dashboard's container filter without a round trip.
    """
    if variable.get("type") == "custom":
        query = variable.get("query")
        if not isinstance(query, str):
            return []
        return [value.strip() for value in query.split(",") if value.strip()]

    current = _mapping(variable.get("current", {})).get("value")
    selected = cast(list[object], current if isinstance(current, list) else [current])
    if _ALL_SENTINEL not in selected:
        return [value for value in selected if isinstance(value, str)]

    all_value = variable.get("allValue")
    if not isinstance(all_value, str) or not all_value.strip():
        raise SystemExit(
            f"variable {variable.get('name')!r} ships with All selected and"
            + " declares no allValue, so its values cannot be resolved"
            + " without asking the datasource"
        )
    return [all_value]


def _interpolate(sql: str, variables: dict[str, list[str]]) -> str:
    """Substitute dashboard variables the way Grafana's frontend does.

    `${name:singlequote}` expands to every value, quoted and comma-joined —
    that is how the multi-valued room selector reaches `IN (...)`. A bare
    `$name` takes the first value, which is what a single-valued dropdown
    shows on load.
    """

    def formatted(match: re.Match[str]) -> str:
        name, fmt = match.group(1), match.group(2)
        values = variables.get(name)
        if values is None:
            return match.group(0)
        if fmt == "singlequote":
            return ",".join(f"'{value}'" for value in values)
        return ",".join(values)

    def plain(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if name.startswith("__"):  # a Grafana macro, not a variable
            return match.group(0)
        values = variables.get(name)
        return values[0] if values else match.group(0)

    return _PLAIN_VARIABLE.sub(plain, _FORMATTED_VARIABLE.sub(formatted, sql))


def _tls_context(cacert: str | None) -> ssl.SSLContext | None:
    """A context trusting `cacert`, or None to use the system trust store.

    The stack's own CA signs its certificates, and a private root is in no
    system store — so without this the proxy is unreachable rather than
    merely untrusted.
    """
    if cacert is None:
        return None
    if not Path(cacert).is_file():
        raise SystemExit(f"--cacert points at {cacert!r}, which is not a file")
    return ssl.create_default_context(cafile=cacert)


def _post(
    payload: dict[str, object],
    password: str,
    endpoint: str,
    context: ssl.SSLContext | None,
) -> dict[str, object]:
    credentials = base64.b64encode(f"admin:{password}".encode()).decode()
    # The endpoint is this script's own localhost constant.
    request = urllib.request.Request(  # noqa: S310
        endpoint,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {credentials}",
        },
    )
    # urlopen resolves to `Any` here, so both the handle and its read are
    # pinned explicitly rather than letting `Any` leak into the parsing below.
    # The same endpoint, built just above.
    opened = urllib.request.urlopen(request, timeout=30, context=context)  # noqa: S310  # pyright: ignore[reportAny]
    with opened as response:  # pyright: ignore[reportAny]
        body = cast(bytes, response.read())  # pyright: ignore[reportAny]
    return cast(dict[str, object], json.loads(body.decode()))


def _child(value: object, key: str) -> object:
    """`value[key]` when `value` is a mapping, `None` otherwise.

    Grafana's response is nested five levels deep and every level is
    `object` as far as the type checker is concerned. Walking it with these
    two helpers keeps the shape assumptions in one place instead of
    spreading `isinstance` ladders through the caller.
    """
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value).get(key)


def _first(value: object) -> object:
    if not isinstance(value, list):
        return None
    items = cast(list[object], value)
    return items[0] if items else None


def _row_count(result: object) -> int:
    """Rows across every frame of a Grafana query result.

    Every frame rather than the first: a Loki range query returns one per
    stream, and an empty leading frame would otherwise read as a dead
    query. A SQL result has a single frame, so nothing changes for it.
    """
    frames = _child(result, "frames")
    if not isinstance(frames, list):
        return 0
    counted = 0
    for frame in cast(list[object], frames):
        column = _first(_child(_child(frame, "data"), "values"))
        if isinstance(column, list):
            counted += len(cast(list[object], column))
    return counted


class _Rejected(Exception):
    """A failure no amount of waiting will fix, so the retry loop gives up."""


class _Query(NamedTuple):
    """One panel target, ready to send and to report on."""

    label: str
    payload: dict[str, object]
    may_be_empty: bool


def _payload(
    kind: str, target: dict[str, object], variables: dict[str, list[str]]
) -> dict[str, object]:
    """One target as its datasource backend expects to receive it.

    The backends differ in more than the key the query sits under.
    InfluxDB wants the frame `format`; Loki wants the `queryType` that
    decides between a range and an instant query, and a line limit. Send
    one's keys to the other and the panel comes back empty rather than
    complaining.
    """
    field = _QUERY_FIELD[kind]
    common: dict[str, object] = {
        "refId": str(target["refId"]),
        "datasource": target["datasource"],
        field: _interpolate(str(target[field]), variables),
        "intervalMs": 1000,
        "maxDataPoints": 1000,
    }
    if kind == "loki":
        return {
            **common,
            "queryType": target.get("queryType", "range"),
            "maxLines": _LOG_LINE_LIMIT,
        }
    return {**common, "format": target.get("format", "time_series")}


def _load_queries(path: Path, dashboard: dict[str, object]) -> list[_Query]:
    """Every panel target on one dashboard, ready for the query endpoint."""
    templating = _mapping(dashboard["templating"])
    variables = {
        str(variable["name"]): _values_of(variable)
        for variable in _mappings(templating["list"])
    }

    queries: list[_Query] = []
    for panel in _mappings(dashboard["panels"]):
        title = str(panel.get("title", ""))
        for target in _mappings(panel.get("targets", [])):
            kind = str(_mapping(target.get("datasource", {})).get("type"))
            if kind not in _QUERY_FIELD:
                raise SystemExit(
                    f"{path.name}: {title!r} targets a {kind!r} datasource,"
                    + " whose query field this script does not know; add it"
                    + f" to _QUERY_FIELD (it knows {sorted(_QUERY_FIELD)})"
                )
            queries.append(
                _Query(
                    label=f"{path.stem}/{title} [{target.get('refId')}]",
                    payload=_payload(kind, target, variables),
                    may_be_empty=(path.stem, title) in _MAY_BE_EMPTY,
                )
            )
    return queries


def _attempt(
    queries: list[_Query],
    window: dict[str, str],
    password: str,
    endpoint: str,
    context: ssl.SSLContext | None,
) -> list[tuple[str, str]]:
    """Run every query once, returning `(panel label, what went wrong)`.

    The two are kept apart so the report can group by the reason: when the
    stack is down, every query shares one reason and differs only by label.
    """
    failures: list[tuple[str, str]] = []
    for query in queries:
        try:
            response = _post(
                {"queries": [query.payload], **window}, password, endpoint, context
            )
        except urllib.error.HTTPError as http_error:
            # The status line alone says nothing useful — a bad column name
            # and a bad token are both "400 Bad Request". The body carries
            # what the datasource actually objected to.
            detail = f"{http_error} — {http_error.read().decode()[:400]}"
            if http_error.code in _TERMINAL_STATUSES:
                raise _Rejected(f"{query.label}: {detail}") from http_error
            failures.append((query.label, detail))
            continue
        except urllib.error.URLError as url_error:
            failures.append((query.label, str(url_error)))
            continue

        result = _child(_child(response, "results"), str(query.payload["refId"]))
        reported = _child(result, "error")
        if reported:
            failures.append((query.label, str(reported)))
        elif _row_count(result) == 0 and not query.may_be_empty:
            failures.append((query.label, "query succeeded but returned no rows"))
    return failures


def _report(
    failures: list[tuple[str, str]],
    queries: list[_Query],
    timeout: float,
    attempts: int,
) -> None:
    """Print the failures, one entry per distinct reason.

    When the stack itself is the problem every query fails the same way, and
    twenty copies of one Flight SQL traceback bury the case this exists to
    show: a single panel broken among working ones.
    """
    summary = (
        f"{len(failures)} of {len(queries)} dashboard queries still failing"
        + f" after {timeout:.0f}s ({attempts} attempts):"
    )
    print(summary, file=sys.stderr)

    by_reason: dict[str, list[str]] = {}
    for label, reason in failures:
        by_reason.setdefault(reason, []).append(label)
    for reason, labels in by_reason.items():
        print(f"  - {', '.join(labels)}: {reason}", file=sys.stderr)

    # Only when a whole dashboard went down at once. Against a single
    # broken panel among working ones it points at the wrong thing, which
    # is worse than saying nothing.
    asked = Counter(query.label.split("/", 1)[0] for query in queries)
    failed = Counter(label.split("/", 1)[0] for label, _ in failures)
    if not any(failed[name] == count for name, count in asked.items()):
        return
    hint = (
        "\nA whole dashboard failing at once usually means the profile behind"
        + " its datasource is not running — the Logs dashboard needs `logs`."
        + " Use --dashboard to check a subset."
    )
    print(hint, file=sys.stderr)


def _selected(wanted: list[str] | None) -> list[Path]:
    """The dashboards to check, every one of them unless `--dashboard` said.

    An unknown name is an error rather than an empty run: a typo would
    otherwise report success having checked nothing at all.
    """
    found = {path.stem: path for path in sorted(_DASHBOARD_DIR.glob("*.json"))}
    if not found:
        raise SystemExit(f"no dashboards found in {_DASHBOARD_DIR}")
    if wanted is None:
        return list(found.values())

    unknown = sorted(set(wanted) - set(found))
    if unknown:
        raise SystemExit(
            f"no dashboard named {', '.join(unknown)} in {_DASHBOARD_DIR}"
            + f" (it holds {sorted(found)})"
        )
    return [found[name] for name in sorted(set(wanted))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="how long to keep retrying while the sensors fill the window",
    )
    _ = parser.add_argument(
        "--password",
        default=os.environ.get("GRAFANA_ADMIN_PASSWORD") or "admin",
        help="Grafana admin password; defaults to $GRAFANA_ADMIN_PASSWORD",
    )
    _ = parser.add_argument(
        "--url",
        default=_DEFAULT_GRAFANA_URL,
        help="Grafana base URL; point at the tls proxy to check that route",
    )
    _ = parser.add_argument(
        "--cacert",
        default=None,
        help="CA certificate to verify --url against, for the tls profile",
    )
    _ = parser.add_argument(
        "--dashboard",
        action="append",
        metavar="NAME",
        help="check only this dashboard, named by its file stem; repeatable."
        + " The default is every dashboard in grafana/dashboards, which needs"
        + " every profile backing one to be running",
    )
    args = parser.parse_args()
    timeout = cast(float, args.timeout)
    password = cast(str, args.password)
    endpoint = f"{cast(str, args.url).rstrip('/')}/api/ds/query"
    context = _tls_context(cast("str | None", args.cacert))

    paths = _selected(cast("list[str] | None", args.dashboard))
    queries: list[_Query] = []
    for path in paths:
        # `json.loads` is typed as returning `Any`; nothing here wants that.
        parsed: object = json.loads(path.read_text("utf-8"))  # pyright: ignore[reportAny]
        queries.extend(_load_queries(path, _mapping(parsed)))

    # The dashboard's own window. Wide enough that a query is judged on
    # whether it works, not on how long the stack has been up.
    window = {"from": "now-15m", "to": "now"}

    deadline = time.monotonic() + timeout
    attempts = 0
    while True:
        attempts += 1
        try:
            failures = _attempt(queries, window, password, endpoint, context)
        except _Rejected as rejected:
            print(f"Grafana rejected the credentials: {rejected}", file=sys.stderr)
            return 1

        if not failures:
            exempt = sum(1 for query in queries if query.may_be_empty)
            note = f", not counting {exempt} allowed to be empty" if exempt else ""
            plural = "" if len(paths) == 1 else "s"
            print(
                f"all {len(queries)} queries across {len(paths)} dashboard"
                + f"{plural} returned rows{note}"
            )
            return 0

        if time.monotonic() >= deadline:
            _report(failures, queries, timeout, attempts)
            return 1

        print(f"{len(failures)} not ready yet, retrying...", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
