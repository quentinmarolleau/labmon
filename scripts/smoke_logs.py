"""Assert Loki has collected a log line from every running container.

The dashboard smoke test proves measurements reach Grafana. This proves
the other half: that the `logs` profile actually collects, which nothing
else checks. Alloy failing to parse its config, Loki rejecting pushes, an
image tag going stale, or a container buffering its output so hard that
nothing is ever written — all of them look like silence, and silence is
indistinguishable from a quiet lab.

Comparing against the running containers, rather than checking the list
is merely non-empty, is what makes it a real assertion. A single missing
container is the interesting failure: `PYTHONUNBUFFERED=1` exists because
without it the six mock sensors are invisible to Docker itself, and a
non-empty check would have passed happily while half the stack went
uncollected.

Queries run through Grafana's datasource proxy rather than straight at
Loki. Loki is deliberately not published to the host, so this is the path
that is actually reachable — and it exercises the same route a person
clicking around Explore takes, which is worth more than a direct hit.

Stdlib only, so CI can run it without installing the project.

Usage:
    python3 scripts/smoke_logs.py [--timeout SECONDS] [--password PASSWORD]

The password defaults to $GRAFANA_ADMIN_PASSWORD, then to `admin`. Note
that `.env` is read by Compose, not by your shell, so a password set
there has to be exported or passed explicitly.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import cast

_GRAFANA_URL = "http://127.0.0.1:3000"
# Grafana proxies to the datasource provisioned under this uid, so the
# path after it is Loki's own API.
_LABEL_VALUES = (
    f"{_GRAFANA_URL}/api/datasources/proxy/uid/loki/loki/api/v1/label/container/values"
)

# Rejecting the credentials will never succeed by waiting, and Grafana
# blocks an account after five consecutive failures — retrying one would
# lock the admin out of the UI as well.
_TERMINAL_STATUSES = frozenset({401, 403})


class _Rejected(Exception):
    """A failure no amount of waiting will fix."""


def _running_containers() -> set[str]:
    """Container names Docker currently reports as running."""
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return {name for name in result.stdout.split("\n") if name.strip()}


def _collected_containers(password: str) -> set[str]:
    """Container names Loki has at least one line for."""
    credentials = base64.b64encode(f"admin:{password}".encode()).decode()
    request = urllib.request.Request(
        _LABEL_VALUES, headers={"Authorization": f"Basic {credentials}"}
    )
    try:
        # urlopen resolves to `Any`; pin the handle and its read explicitly.
        with urllib.request.urlopen(request, timeout=30) as response:  # pyright: ignore[reportAny]
            body = cast(bytes, response.read())  # pyright: ignore[reportAny]
    except urllib.error.HTTPError as error:
        detail = f"{error} — {error.read().decode()[:300]}"
        if error.code in _TERMINAL_STATUSES:
            raise _Rejected(detail) from error
        raise
    payload = cast(dict[str, object], json.loads(body.decode()))
    values = payload.get("data")
    if not isinstance(values, list):
        return set()
    return {str(value) for value in cast(list[object], values)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="how long to keep retrying while Alloy discovers and ships",
    )
    _ = parser.add_argument(
        "--password",
        default=os.environ.get("GRAFANA_ADMIN_PASSWORD") or "admin",
        help="Grafana admin password; defaults to $GRAFANA_ADMIN_PASSWORD",
    )
    args = parser.parse_args()
    timeout = cast(float, args.timeout)
    password = cast(str, args.password)

    running = _running_containers()
    if not running:
        print("no running containers — is the stack up?", file=sys.stderr)
        return 1

    deadline = time.monotonic() + timeout
    missing: set[str] = set(running)
    while True:
        try:
            collected = _collected_containers(password)
        except _Rejected as rejected:
            print(f"Grafana rejected the credentials: {rejected}", file=sys.stderr)
            return 1
        except (urllib.error.URLError, OSError) as error:
            # Loki has no healthcheck, so the first attempts may land
            # before it is ready. That is expected, not a failure.
            collected: set[str] = set()
            print(f"not ready yet ({error}), retrying...", flush=True)

        missing = running - collected
        if not missing:
            print(f"all {len(running)} running containers have logs in Loki")
            return 0

        if time.monotonic() >= deadline:
            print(
                f"{len(missing)} of {len(running)} containers have no logs in Loki"
                + f" after {timeout:.0f}s:",
                file=sys.stderr,
            )
            for name in sorted(missing):
                print(f"  - {name}", file=sys.stderr)
            hint = (
                "\nA container missing here is usually buffering rather than a"
                + " collection fault: check `docker compose logs <name>` shows"
                + " anything at all."
            )
            print(hint, file=sys.stderr)
            return 1

        print(f"{len(missing)} not collected yet, retrying...", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
