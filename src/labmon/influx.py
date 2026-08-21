"""Shared InfluxDB connection settings, read from the environment."""

import logging
import os
from pathlib import Path

from influxdb_client_3 import InfluxDBClient3

logger: logging.Logger = logging.getLogger(__name__)


def _setting(name: str, default: str) -> str:
    """Value of `name`, falling back when it is unset *or* empty.

    `os.environ.get(name, default)` falls back only when the variable is
    absent, which is not how the rest of the stack behaves. `.env.example`
    ships INFLUXDB_DATABASE empty and Compose reads it as
    `${INFLUXDB_DATABASE:-lab}`, whose default applies to an empty value
    too. Anything host-side sourcing that same file has to agree, or it
    connects to a database named "" and fails with a server-side error
    that names neither the variable nor the value.
    """
    return os.environ.get(name) or default


INFLUXDB_HOST = _setting("INFLUXDB_HOST", "http://localhost:8181")
INFLUXDB_DATABASE = _setting("INFLUXDB_DATABASE", "lab")


def get_client() -> InfluxDBClient3:
    """Build an InfluxDB client for the configured host and database.

    Set INFLUXDB_TLS_CA to the certificate a private CA signs the server
    with, as `scripts/export-ca.sh` produces — needed when the server runs
    behind the `tls` profile's proxy, since a private root is not in any
    system trust store. Unset, nothing about the client changes, so a
    deployment still speaking plain HTTP is unaffected.

    One variable covers both directions of traffic: the same file is read
    by the HTTP write client and by the Flight SQL query client. It only
    does anything when INFLUXDB_HOST is an https:// address, and says so
    at WARNING when it is not.

    Raises KeyError if INFLUXDB3_AUTH_TOKEN is not set, and
    FileNotFoundError if INFLUXDB_TLS_CA names a file that is not there.
    """
    options: dict[str, str] = {}
    ca = os.environ.get("INFLUXDB_TLS_CA")
    if ca:
        # Checked here rather than left to the TLS layer, which reports a
        # missing bundle as a verification failure against the server —
        # sending the reader after a certificate problem that does not
        # exist, instead of the typo in this machine's env file.
        if not Path(ca).is_file():
            raise FileNotFoundError(
                f"INFLUXDB_TLS_CA points at {ca!r}, which is not a file."
                + " It should be the CA certificate exported from the server"
                + " (see scripts/export-ca.sh)."
            )
        # A CA against a plain-HTTP host is accepted and then ignored,
        # which is the one way this setting fails quietly: the operator
        # believes the link is encrypted and it is not. Warn rather than
        # raise, because pointing a sensor at a local plain stack while
        # the shared env file still names a CA is a legitimate thing to
        # do — it just should not look like it is protected.
        if not INFLUXDB_HOST.lower().startswith("https://"):
            logger.warning(
                "ignoring INFLUXDB_TLS_CA because the host is not https",
                extra={"host": INFLUXDB_HOST, "ca": ca},
            )

        options["ssl_ca_cert"] = ca

    return InfluxDBClient3(
        host=INFLUXDB_HOST,
        token=os.environ["INFLUXDB3_AUTH_TOKEN"],
        database=INFLUXDB_DATABASE,
        **options,
    )
