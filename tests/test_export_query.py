"""Building and running the SQL an export needs."""

from datetime import UTC, datetime
from typing import cast

import pyarrow as pa
import pytest

from labmon.export.query import (
    QueryError,
    columns_of,
    fetch,
    list_measurements,
    resolve_measurements,
)
from labmon.export.window import Window

_WINDOW = Window(
    since=datetime(2026, 8, 1, tzinfo=UTC), until=datetime(2026, 8, 2, tzinfo=UTC)
)

_TABLES = pa.table(
    {
        "table_schema": pa.array(["iox", "iox", "information_schema", "system", "iox"]),
        "table_name": pa.array(
            ["temperature", "pressure", "columns", "queries", "voltage"]
        ),
    }
)


class FakeClient:
    """Records the SQL and parameters it was given, and replays answers."""

    def __init__(self, columns: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self._columns: tuple[str, ...] = columns

    def query(
        self,
        query: str,
        language: str = "sql",
        mode: str = "all",
        database: str | None = None,
        **kwargs: object,
    ) -> pa.Table:
        # Named to match the client protocol; the fake needs none of them.
        _ = (language, mode, database)
        raw = kwargs.get("query_parameters")
        parameters: dict[str, str] = (
            cast(dict[str, str], raw) if isinstance(raw, dict) else {}
        )
        self.calls.append((query, dict(parameters)))
        if "SHOW TABLES" in query:
            return _TABLES
        if "information_schema.columns" in query:
            return pa.table({"column_name": pa.array(list(self._columns))})
        return pa.table({"time": pa.array([], pa.timestamp("ns"))})

    def close(self) -> None:
        return None


_FULL = ("time", "value", "sensor_id", "unit", "calibration_id", "input_volts")


def test_only_user_tables_count_as_measurements() -> None:
    # SHOW TABLES also lists information_schema and system tables. Without
    # the filter, exporting "everything" would export the server's own
    # query log alongside the readings.
    assert list_measurements(FakeClient()) == ("pressure", "temperature", "voltage")


def test_measurements_come_back_sorted() -> None:
    # So an export of everything concatenates in a stable order and two
    # runs over unchanged data produce identical files.
    assert list(list_measurements(FakeClient())) == sorted(
        list_measurements(FakeClient())
    )


def test_no_requested_measurements_means_all_of_them() -> None:
    assert resolve_measurements(FakeClient(), []) == (
        "pressure",
        "temperature",
        "voltage",
    )


def test_requested_measurements_keep_the_servers_order() -> None:
    resolved = resolve_measurements(FakeClient(), ["voltage", "pressure"])

    assert resolved == ("pressure", "voltage")


def test_a_repeated_measurement_is_exported_once() -> None:
    assert resolve_measurements(FakeClient(), ["voltage", "voltage"]) == ("voltage",)


def test_an_unknown_measurement_lists_what_is_available() -> None:
    with pytest.raises(QueryError, match="no measurement named 'nope'"):
        _ = resolve_measurements(FakeClient(), ["nope"])


def test_an_unknown_measurement_is_never_interpolated_into_sql() -> None:
    # The allowlist is what makes putting a name in FROM safe. A name the
    # server did not report must not reach a query at all.
    client = FakeClient()
    with pytest.raises(QueryError):
        _ = resolve_measurements(client, ["temperature'; DROP TABLE x--"])

    assert not any("DROP TABLE" in sql for sql, _ in client.calls)


def test_columns_are_asked_for_rather_than_assumed() -> None:
    client = FakeClient(columns=("time", "value"))

    assert columns_of(client, "temperature") == frozenset({"time", "value"})
    sql, parameters = client.calls[0]
    assert "information_schema.columns" in sql
    assert parameters["table"] == "temperature"


def test_a_select_names_only_the_columns_that_exist() -> None:
    # SELECT unit FROM a table with no unit is a hard error from the
    # server, so the column list decides what the SELECT contains.
    client = FakeClient(columns=("time", "value"))

    _ = fetch(client, "probe", _WINDOW)

    sql = client.calls[-1][0]
    assert '"time", "value"' in sql
    assert "unit" not in sql


def test_a_select_picks_up_every_optional_column_present() -> None:
    client = FakeClient(columns=_FULL)

    _ = fetch(client, "temperature", _WINDOW)

    sql = client.calls[-1][0]
    for name in ("sensor_id", "unit", "calibration_id", "input_volts"):
        assert f'"{name}"' in sql


def test_time_bounds_are_bound_as_parameters() -> None:
    client = FakeClient(columns=_FULL)

    _ = fetch(client, "temperature", _WINDOW)

    sql, parameters = client.calls[-1]
    assert "$since" in sql
    assert "$until" in sql
    assert parameters["since"] == "2026-08-01T00:00:00+00:00"
    assert parameters["until"] == "2026-08-02T00:00:00+00:00"


def test_the_window_is_half_open() -> None:
    # Half-open so two back-to-back exports neither duplicate nor drop the
    # reading that lands exactly on the boundary.
    client = FakeClient(columns=_FULL)

    _ = fetch(client, "temperature", _WINDOW)

    sql = client.calls[-1][0]
    assert '"time" >= CAST($since AS TIMESTAMP)' in sql
    assert '"time" < CAST($until AS TIMESTAMP)' in sql


def test_sensor_ids_are_bound_as_parameters_not_interpolated() -> None:
    client = FakeClient(columns=_FULL)

    _ = fetch(client, "temperature", _WINDOW, ["cryo-77k", "x'; DROP TABLE y--"])

    sql, parameters = client.calls[-1]
    assert "DROP TABLE" not in sql
    assert '"sensor_id" IN ($sensor_0, $sensor_1)' in sql
    assert parameters["sensor_0"] == "cryo-77k"
    assert parameters["sensor_1"] == "x'; DROP TABLE y--"


def test_no_sensor_filter_leaves_the_clause_out() -> None:
    client = FakeClient(columns=_FULL)

    _ = fetch(client, "temperature", _WINDOW)

    assert "sensor_id IN" not in client.calls[-1][0]


def test_rows_come_back_in_time_order() -> None:
    client = FakeClient(columns=_FULL)

    _ = fetch(client, "temperature", _WINDOW)

    assert 'ORDER BY "time"' in client.calls[-1][0]


def test_a_table_without_readings_is_refused() -> None:
    client = FakeClient(columns=("time", "src"))

    with pytest.raises(QueryError, match="does not hold readings"):
        _ = fetch(client, "probe", _WINDOW)


def test_filtering_a_table_that_records_no_sensor_says_so() -> None:
    # Asking for a sensor from a table with no sensor_id can only ever
    # return nothing; saying so beats handing back an empty file.
    client = FakeClient(columns=("time", "value"))

    with pytest.raises(QueryError, match="no sensor_id column"):
        _ = fetch(client, "probe", _WINDOW, ["cryo-77k"])
