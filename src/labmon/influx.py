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


INFLUXDB_HOST = _setting("INFLUXDB_HOST", "http://localhost:8181")
INFLUXDB_DATABASE = _setting("INFLUXDB_DATABASE", "lab")


def get_client() -> InfluxDBClient3:
    """Build an InfluxDB client for the configured host and database.

    Raises KeyError if INFLUXDB3_AUTH_TOKEN is not set.
    """
    return InfluxDBClient3(
        host=INFLUXDB_HOST,
        token=os.environ["INFLUXDB3_AUTH_TOKEN"],
        database=INFLUXDB_DATABASE,
    )
