"""Shared InfluxDB connection settings, read from the environment."""

import os

from influxdb_client_3 import InfluxDBClient3


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


def influx_host() -> str:
    """Where readings are written, from INFLUXDB_HOST.

    A function rather than a module constant so the value is resolved when it
    is asked for. Resolved at import time it would silently pin whatever was
    set at that moment: a test, a library embedding labmon, or anyone
    following docs/custom-sensor.md who imports before configuring gets a
    stale value, and the symptom is "it connects to the wrong database and I
    cannot see why" rather than an error.
    """
    return _setting("INFLUXDB_HOST", "http://localhost:8181")


def influx_database() -> str:
    """Database readings are written to, from INFLUXDB_DATABASE.

    Resolved per call, for the reason given on `influx_host`.
    """
    return _setting("INFLUXDB_DATABASE", "lab")


def get_client() -> InfluxDBClient3:
    """Build an InfluxDB client for the configured host and database.

    Raises KeyError if INFLUXDB3_AUTH_TOKEN is not set.
    """
    return InfluxDBClient3(
        host=influx_host(),
        token=os.environ["INFLUXDB3_AUTH_TOKEN"],
        database=influx_database(),
    )
