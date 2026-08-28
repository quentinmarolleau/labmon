"""Run every dashboard query against a live stack and assert it returns rows.

The unit tests in `tests/test_dashboard.py` check the dashboard's shape.
This checks that it *works*: it takes each panel's `rawSql`, substitutes the
dashboard variables the way Grafana's frontend would, and sends it through
Grafana's own query endpoint — same datasource, same token, same Flight SQL
connection a browser would use.

That covers the gap nothing else does. A query can be structurally perfect
and still name a column InfluxDB never created, or reference a table only a
mock sensor writes; both render as an empty panel and log nothing. Here they
fail out loud.

Stdlib only, so CI can run it without installing the project.

Usage:
    python3 scripts/smoke_dashboard.py [--timeout SECONDS] [--password PASSWORD]

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
from pathlib import Path
from typing import cast

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD = _REPO_ROOT / "grafana" / "dashboards" / "lab-overview.json"

# Where Grafana is by default. `--url` points this at the `tls` profile's
# proxy instead (https://127.0.0.1:3443), which is the same Grafana reached
# by the route a viewer actually uses once TLS is on.
_DEFAULT_GRAFANA_URL = "http://127.0.0.1:3000"

# Grafana blocks an account after five consecutive bad passwords, so retrying
# one is worse than useless: three minutes of retries would lock the admin out
# of the UI as well, long after the password was corrected.
_TERMINAL_STATUSES = frozenset({401, 403})

# Grafana's own macros ($__timeFrom() and friends) are expanded server-side by
# the datasource backend, so they are left alone. Dashboard variables are not:
# the frontend substitutes those before the query is ever sent, which is what
# this has to reproduce.
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
    """A custom variable's values come from its comma-separated `query`."""
    query = variable.get("query")
    if not isinstance(query, str):
        return []
    return [value.strip() for value in query.split(",") if value.strip()]


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
    """Rows in the first frame of a Grafana query result, 0 if there are none."""
    frame = _first(_child(result, "frames"))
    first_column = _first(_child(_child(frame, "data"), "values"))
    if not isinstance(first_column, list):
        return 0
    return len(cast(list[object], first_column))


class _Rejected(Exception):
    """A failure no amount of waiting will fix, so the retry loop gives up."""


def _load_queries(dashboard: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    """Every panel target, as a payload for Grafana's query endpoint."""
    templating = _mapping(dashboard["templating"])
    variables = {
        str(variable["name"]): _values_of(variable)
        for variable in _mappings(templating["list"])
    }

    return [
        (
            f"{panel['title']} [{target.get('refId')}]",
            {
                "refId": str(target["refId"]),
                "datasource": target["datasource"],
                "rawSql": _interpolate(str(target["rawSql"]), variables),
                "format": target.get("format", "time_series"),
                "intervalMs": 1000,
                "maxDataPoints": 1000,
            },
        )
        for panel in _mappings(dashboard["panels"])
        for target in _mappings(panel.get("targets", []))
    ]


def _attempt(
    queries: list[tuple[str, dict[str, object]]],
    window: dict[str, str],
    password: str,
    endpoint: str,
    context: ssl.SSLContext | None,
) -> list[tuple[str, str]]:
    """Run every query once, returning `(panel label, what went wrong)`.

    The two are kept apart so the report can group by the reason: when the
    stack is down, all fourteen share one reason and differ only by label.
    """
    failures: list[tuple[str, str]] = []
    for label, query in queries:
        try:
            response = _post(
                {"queries": [query], **window}, password, endpoint, context
            )
        except urllib.error.HTTPError as http_error:
            # The status line alone says nothing useful — a bad column name
            # and a bad token are both "400 Bad Request". The body carries
            # what the datasource actually objected to.
            detail = f"{http_error} — {http_error.read().decode()[:400]}"
            if http_error.code in _TERMINAL_STATUSES:
                raise _Rejected(f"{label}: {detail}") from http_error
            failures.append((label, detail))
            continue
        except urllib.error.URLError as url_error:
            failures.append((label, str(url_error)))
            continue

        result = _child(_child(response, "results"), str(query["refId"]))
        reported = _child(result, "error")
        if reported:
            failures.append((label, str(reported)))
        elif _row_count(result) == 0:
            failures.append((label, "query succeeded but returned no rows"))
    return failures


def _report(
    failures: list[tuple[str, str]], total: int, timeout: float, attempts: int
) -> None:
    """Print the failures, one entry per distinct reason.

    When the stack itself is the problem every query fails the same way, and
    fourteen copies of one Flight SQL traceback buries the case this exists
    to show: a single panel broken among working ones.
    """
    summary = (
        f"{len(failures)} of {total} dashboard queries still failing after"
        + f" {timeout:.0f}s ({attempts} attempts):"
    )
    print(summary, file=sys.stderr)

    by_reason: dict[str, list[str]] = {}
    for label, reason in failures:
        by_reason.setdefault(reason, []).append(label)
    for reason, labels in by_reason.items():
        print(f"  - {', '.join(labels)}: {reason}", file=sys.stderr)


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
    args = parser.parse_args()
    timeout = cast(float, args.timeout)
    password = cast(str, args.password)
    endpoint = f"{cast(str, args.url).rstrip('/')}/api/ds/query"
    context = _tls_context(cast("str | None", args.cacert))

    # `json.loads` is typed as returning `Any`; nothing here wants that.
    parsed: object = json.loads(_DASHBOARD.read_text("utf-8"))  # pyright: ignore[reportAny]
    queries = _load_queries(_mapping(parsed))

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
            print(f"all {len(queries)} dashboard queries returned rows")
            return 0

        if time.monotonic() >= deadline:
            _report(failures, len(queries), timeout, attempts)
            return 1

        print(f"{len(failures)} not ready yet, retrying...", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
