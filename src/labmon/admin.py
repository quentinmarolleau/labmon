"""Set up an InfluxDB 3 instance: its admin token, and its database.

Everything here talks to `/api/v3/configure/...` over plain HTTP rather
than through `InfluxDBClient3`, for two reasons. The first is ordering:
the token endpoint is what *issues* the credential every other call
needs, so it has to work before there is a client to build. The second
is weight — the client pulls in pyarrow and costs about 0.3s to import,
which is a lot for a command whose whole job is three requests.

Only the operations labmon actually offers are here. InfluxDB 3 Core can
also create tables, caches and triggers; none of that is labmon's
business, and wrapping it would mean maintaining a second, worse
`influxdb3` CLI.

What Core *cannot* do is delete rows: its delete granularity is a
database, a table, a cache, a trigger or a token, and there is no
`DELETE ... WHERE`. That is why there is no `--since`/`--sensor-id`
option anywhere in this module, and why resetting means dropping the
database rather than deleting what is in it.
"""

import json
import logging
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import cast

logger: logging.Logger = logging.getLogger(__name__)

# Long enough for a server still opening its object store, short enough
# that a wrong host fails while somebody is still watching.
TIMEOUT_SECONDS = 30.0

# An endpoint, not a credential — S105 matches the name, not the value.
_ADMIN_TOKEN_PATH = "/api/v3/configure/token/admin"  # noqa: S105
_DATABASE_PATH = "/api/v3/configure/database"
_QUERY_PATH = "/api/v3/query_sql"

# The database holding the server's own catalogue. `system.databases`
# there is the only place a retention period can be read back: the
# configure endpoint lists names and nothing else.
_INTERNAL_DATABASE = "_internal"

# Every database the server serves, with its retention. `deleted = false`
# drops the tombstones a soft delete leaves behind, so a database dropped
# and recreated under the same name is reported once.
_RETENTION_QUERY = (
    "SELECT database_name, retention_period_ns FROM system.databases"
    " WHERE deleted = false"
)

_NANOSECONDS_PER_DAY = 86_400_000_000_000


class AdminError(Exception):
    """A request the server refused, in terms the reader can act on."""


def _tls_context() -> ssl.SSLContext | None:
    """Trust the stack's own CA when INFLUXDB_TLS_CA names it.

    The same variable `labmon.influx.get_client` reads, so a deployment
    behind the `tls` profile needs configuring once rather than twice. A
    private root is in no system store, so without this the proxy is
    unreachable rather than merely untrusted.
    """
    ca = os.environ.get("INFLUXDB_TLS_CA")
    if not ca:
        return None
    if not Path(ca).is_file():
        raise AdminError(
            f"INFLUXDB_TLS_CA points at {ca!r}, which is not a file."
            + " It should be the CA certificate exported from the server"
            + " (see scripts/export-ca.sh)."
        )
    return ssl.create_default_context(cafile=ca)


def _request(
    method: str,
    host: str,
    path: str,
    *,
    token: str | None = None,
    body: dict[str, object] | None = None,
    query: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    """One call to the configure API, returning its status and body.

    A 4xx comes back as a value rather than an exception because the
    statuses that matter here are ordinary outcomes: 409 means the thing
    already exists, which is what a second `labmon init` should report
    calmly rather than fail on.
    """
    url = f"{host.rstrip('/')}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    # The URL is built from the configured host and this module's own
    # constants, never from a response.
    request = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310

    try:
        # urlopen resolves to `Any`; the handle and its read are pinned.
        opened = urllib.request.urlopen(  # noqa: S310  # pyright: ignore[reportAny]
            request, timeout=TIMEOUT_SECONDS, context=_tls_context()
        )
        with opened as response:  # pyright: ignore[reportAny]
            return cast(int, response.status), cast(bytes, response.read())  # pyright: ignore[reportAny]
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except urllib.error.URLError as error:
        raise AdminError(f"cannot reach {host}: {error.reason}") from error


def _refuse(status: int, body: bytes, doing: str) -> None:
    """Turn an unexpected status into a message naming what failed.

    The server's own text is quoted: for a malformed retention it says
    `invalid value: string "banana", expected a duration`, which is more
    use than anything this module could invent.
    """
    detail = body.decode(errors="replace").strip() or f"HTTP {status}"
    raise AdminError(f"{doing}: {detail}")


def create_admin_token(host: str) -> str | None:
    """Issue the instance's admin token, or None if it already has one.

    Unauthenticated, because this is the call that bootstraps
    authentication. It works exactly once per instance: the second
    attempt is refused with 409 and the original token is not recoverable
    from the server, which is why `labmon init` writes it to `.env`
    rather than printing it and hoping.
    """
    status, body = _request("POST", host, _ADMIN_TOKEN_PATH)
    if status == 409:
        return None
    if status not in (200, 201):
        _refuse(status, body, "could not create an admin token")
    payload = cast(dict[str, object], json.loads(body.decode()))
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise AdminError("the server issued a token with no value in it")
    return token


def create_database(host: str, token: str, name: str, retention: str | None) -> bool:
    """Create `name`, returning False if it was already there.

    `retention` is a duration the server parses — `1y`, `30d`, `24h` —
    or None for unlimited, which is also what a database gets when a
    write brings it into existence on its own.
    """
    body: dict[str, object] = {"db": name, "retention_period": retention}
    status, response = _request("POST", host, _DATABASE_PATH, token=token, body=body)
    if status == 409:
        return False
    if status not in (200, 201):
        _refuse(status, response, f"could not create the database {name!r}")
    return True


def set_retention(host: str, token: str, name: str, retention: str | None) -> None:
    """Change how long `name` keeps readings, on a database that exists."""
    body: dict[str, object] = {"db": name, "retention_period": retention}
    status, response = _request("PUT", host, _DATABASE_PATH, token=token, body=body)
    if status not in (200, 201):
        _refuse(status, response, f"could not set the retention on {name!r}")


def delete_database(host: str, token: str, name: str, *, hard: bool = False) -> None:
    """Delete `name` and everything in it.

    Soft by default, which is the server's own default: the data stops
    being queryable at once, and what is on disk is renamed to
    `<name>-<timestamp>` and reclaimed later. The original name is free
    immediately, so a database of the same name can be created straight
    after — which is what lets a reset be a delete followed by a create
    rather than two steps with a wait between them.

    `hard` asks for that space back now rather than on the server's own
    schedule. The renamed copy is a real safety net — an accidental reset
    is recoverable from it until the server clears it — so this is a
    deliberate request rather than the default, and it is the answer for
    a disk that is actually full.

    Either way the catalogue keeps a row for the deletion, which
    `influxdb3 show databases` lists; `hard` shortens how long the data
    behind it lives, not whether the record of it does.
    """
    query = {"db": name}
    if hard:
        query["hard_delete_at"] = "now"
    status, response = _request(
        "DELETE", host, _DATABASE_PATH, token=token, query=query
    )
    if status not in (200, 204):
        _refuse(status, response, f"could not delete the database {name!r}")


def read_retention(host: str, token: str, name: str) -> str | None:
    """The retention `name` currently keeps, as a duration, or None.

    Read so a reset can put back what was there. Without it, resetting a
    database created with `--retention 1y` would quietly return it to
    keeping everything for ever, and nothing would say so until the disk
    filled.

    Reported in whole days, which every value the server accepts from
    labmon resolves to. `1y` is stored as 365.25 days and comes back as
    `365d`; the quarter day is InfluxDB's own rounding of a year, not
    something worth carrying through a reset.
    """
    query = {"db": _INTERNAL_DATABASE, "q": _RETENTION_QUERY, "format": "json"}
    status, body = _request("GET", host, _QUERY_PATH, token=token, query=query)
    if status != 200:
        _refuse(status, body, "could not read the current retention")
    rows = cast(list[dict[str, object]], json.loads(body.decode()))
    for row in rows:
        if row.get("database_name") != name:
            continue
        nanoseconds = row.get("retention_period_ns")
        if not isinstance(nanoseconds, int):
            return None
        return f"{max(1, round(nanoseconds / _NANOSECONDS_PER_DAY))}d"
    return None


def database_exists(host: str, token: str, name: str) -> bool:
    """Whether `name` is a database the server currently serves."""
    query = {"db": _INTERNAL_DATABASE, "q": _RETENTION_QUERY, "format": "json"}
    status, body = _request("GET", host, _QUERY_PATH, token=token, query=query)
    if status != 200:
        _refuse(status, body, "could not list the databases")
    rows = cast(list[dict[str, object]], json.loads(body.decode()))
    return any(row.get("database_name") == name for row in rows)
