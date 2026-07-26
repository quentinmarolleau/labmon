"""Shared InfluxDB connection settings, read from the environment."""

import os

from influxdb_client_3 import InfluxDBClient3

INFLUXDB_HOST = os.environ.get("INFLUXDB_HOST", "http://localhost:8181")
INFLUXDB_DATABASE = os.environ.get("INFLUXDB_DATABASE", "lab")


def get_client() -> InfluxDBClient3:
    """Build an InfluxDB client for the configured host and database.

    Raises KeyError if INFLUXDB3_AUTH_TOKEN is not set.
    """
    return InfluxDBClient3(
        host=INFLUXDB_HOST,
        token=os.environ["INFLUXDB3_AUTH_TOKEN"],
        database=INFLUXDB_DATABASE,
    )
