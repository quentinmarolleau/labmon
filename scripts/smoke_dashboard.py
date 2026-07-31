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
    python3 scripts/smoke_dashboard.py [--timeout SECONDS]
"""

import argparse
import base64
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD = _REPO_ROOT / "grafana" / "dashboards" / "lab-overview.json"

_GRAFANA_URL = "http://127.0.0.1:3000"
_QUERY_ENDPOINT = f"{_GRAFANA_URL}/api/ds/query"

# Grafana's own macros ($__timeFrom() and friends) are expanded server-side by
# the datasource backend, so they are left alone. Dashboard variables are not:
# the frontend substitutes those before the query is ever sent, which is what
# this has to reproduce.
_PLAIN_VARIABLE = re.compile(r"\$\{(\w+)\}|\$(\w+)\b")
_FORMATTED_VARIABLE = re.compile(r"\$\{(\w+):(\w+)\}")


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


def _post(payload: dict[str, object], password: str) -> dict[str, object]:
    credentials = base64.b64encode(f"admin:{password}".encode()).decode()
    request = urllib.request.Request(
        _QUERY_ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {credentials}",
        },
    )
    # urlopen resolves to `Any` here, so both the handle and its read are
    # pinned explicitly rather than letting `Any` leak into the parsing below.
    with urllib.request.urlopen(request, timeout=30) as response:  # pyright: ignore[reportAny]
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
        default="admin",
        help="Grafana admin password (GRAFANA_ADMIN_PASSWORD)",
    )
    args = parser.parse_args()
    timeout = cast(float, args.timeout)
    password = cast(str, args.password)

    dashboard = cast(
        dict[str, object], json.loads(_DASHBOARD.read_text(encoding="utf-8"))
    )
    templating = cast(dict[str, object], dashboard["templating"])
    variables = {
        str(cast(dict[str, object], variable)["name"]): _values_of(
            cast(dict[str, object], variable)
        )
        for variable in cast(list[object], templating["list"])
    }

    queries: list[tuple[str, dict[str, object]]] = []
    for panel in cast(list[object], dashboard["panels"]):
        panel_dict = cast(dict[str, object], panel)
        title = str(panel_dict["title"])
        for target in cast(list[object], panel_dict.get("targets", [])):
            target_dict = cast(dict[str, object], target)
            queries.append(
                (
                    f"{title} [{target_dict.get('refId')}]",
                    {
                        "refId": str(target_dict["refId"]),
                        "datasource": target_dict["datasource"],
                        "rawSql": _interpolate(str(target_dict["rawSql"]), variables),
                        "format": target_dict.get("format", "time_series"),
                        "intervalMs": 1000,
                        "maxDataPoints": 1000,
                    },
                )
            )

    # The dashboard's own window. Wide enough that a query is judged on
    # whether it works, not on how long the stack has been up.
    window = {"from": "now-15m", "to": "now"}

    deadline = time.monotonic() + timeout
    attempt = 0
    while True:
        attempt += 1
        failures: list[str] = []
        for label, query in queries:
            try:
                response = _post({"queries": [query], **window}, password)
            except urllib.error.HTTPError as error:
                # The status line alone says nothing useful — a bad column
                # name and a bad token are both "400 Bad Request". The body
                # carries what the datasource actually objected to.
                failures.append(f"{label}: {error} — {error.read().decode()[:400]}")
                continue
            except urllib.error.URLError as error:
                failures.append(f"{label}: {error}")
                continue

            result = _child(_child(response, "results"), str(query["refId"]))
            error = _child(result, "error")
            if error:
                failures.append(f"{label}: {error}")
            elif _row_count(result) == 0:
                failures.append(f"{label}: query succeeded but returned no rows")

        if not failures:
            print(f"all {len(queries)} dashboard queries returned rows")
            return 0

        if time.monotonic() >= deadline:
            summary = (
                f"{len(failures)} of {len(queries)} dashboard queries still"
                + f" failing after {timeout:.0f}s ({attempt} attempts):"
            )
            print(summary, file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1

        print(f"{len(failures)} not ready yet, retrying...", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
