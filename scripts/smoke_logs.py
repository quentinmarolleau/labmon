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
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast

# Where Grafana is by default. `--url` points this at the `tls` profile's
# proxy instead, which is the route a viewer actually uses once TLS is on.
_DEFAULT_GRAFANA_URL = "http://127.0.0.1:3000"
# Grafana proxies to the datasource provisioned under this uid, so the
# path after it is Loki's own API.
_LABEL_PATH = "/api/datasources/proxy/uid/loki/loki/api/v1/label/container/values"

# Rejecting the credentials will never succeed by waiting, and Grafana
# blocks an account after five consecutive failures — retrying one would
# lock the admin out of the UI as well.
_TERMINAL_STATUSES = frozenset({401, 403})

# Containers held to a lower standard, because they are meant to be quiet.
#
# Loki runs at log_level=warn (see loki/config.yaml) exactly so the thing
# collecting the logs is not itself a log source. What it emits in practice
# is a single line — a transient ring-initialisation error at startup — and
# that error is the only reason it would satisfy a check like this one.
# Requiring it means the suite passes on an upstream bug and starts failing
# the day that bug is fixed, reporting a collection fault that is really a
# log-level setting two files away.
#
# The other eleven containers prove collection works. This one proves
# nothing either way, so it is not asked to.
_EXPECTED_SILENT = frozenset({"loki"})


class _Rejected(Exception):
    """A failure no amount of waiting will fix."""


def _running_containers() -> set[str]:
    """Container names Compose reports as running for this project.

    Scoped to the project rather than asking `docker ps` for the whole
    host: on a workstation that sweeps in every unrelated container and
    holds each one to the same rule, so a quiet one fails the check for
    good. Profiles do not narrow this — Compose lists everything running
    under the project whichever profile started it.
    """
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        raise _Rejected("docker is not on PATH") from None
    except subprocess.CalledProcessError as error:
        # Usually being run from outside the repo, where there is no
        # compose file to read.
        detail = cast(str, error.stderr) or ""
        raise _Rejected(f"`docker compose ps` failed — {detail.strip()}") from error
    return {name for name in result.stdout.split("\n") if name.strip()}


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


def _collected_containers(
    password: str, endpoint: str, context: ssl.SSLContext | None
) -> set[str]:
    """Container names Loki has at least one line for."""
    credentials = base64.b64encode(f"admin:{password}".encode()).decode()
    request = urllib.request.Request(
        endpoint, headers={"Authorization": f"Basic {credentials}"}
    )
    try:
        # urlopen resolves to `Any`; pin the handle and its read explicitly.
        opened = urllib.request.urlopen(request, timeout=30, context=context)  # pyright: ignore[reportAny]
        with opened as response:  # pyright: ignore[reportAny]
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
    endpoint = f"{cast(str, args.url).rstrip('/')}{_LABEL_PATH}"
    context = _tls_context(cast("str | None", args.cacert))

    try:
        started = _running_containers()
    except _Rejected as rejected:
        print(f"cannot list the stack's containers: {rejected}", file=sys.stderr)
        return 1
    if not started:
        print("no running containers — is the stack up?", file=sys.stderr)
        return 1
    running = started - _EXPECTED_SILENT

    deadline = time.monotonic() + timeout
    while True:
        try:
            collected = _collected_containers(password, endpoint, context)
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
            exempt = sorted(started - running)
            note = f", not counting {', '.join(exempt)}" if exempt else ""
            print(f"all {len(running)} running containers have logs in Loki{note}")
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
