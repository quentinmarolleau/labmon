"""Building and running the SQL an export needs."""

from datetime import UTC, datetime
from typing import cast, override

import pyarrow as pa
import pytest

from labmon.export.query import (
    QueryError,
    columns_of,
    fetch,
    fetch_latest,
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


# --------------------------------------------------------------------------
# The latest reading from each sensor
# --------------------------------------------------------------------------


def test_latest_reads_every_measurement_in_one_round_trip() -> None:
    # One query rather than one per table: the round trips are what cost,
    # not the scan. Measured at 84ms for six tables over 24 hours.
    client = FakeClient(_FULL)

    _ = fetch_latest(client, ("temperature", "pressure"), _WINDOW)

    selects = [sql for sql, _ in client.calls if sql.startswith("SELECT '")]
    assert len(selects) == 1
    assert selects[0].count("UNION ALL") == 1


def test_latest_names_the_measurement_each_row_came_from() -> None:
    # The tables are separate, so nothing in the rows themselves says
    # which one a reading belongs to.
    client = FakeClient(_FULL)

    _ = fetch_latest(client, ("temperature",), _WINDOW)

    sql = client.calls[-1][0]
    assert "'temperature' AS \"measurement\"" in sql


def test_latest_takes_the_most_recent_row_per_sensor() -> None:
    client = FakeClient(_FULL)

    _ = fetch_latest(client, ("temperature",), _WINDOW)

    sql = client.calls[-1][0]
    assert "last_value" in sql
    assert 'GROUP BY "sensor_id"' in sql


def test_latest_binds_its_time_bounds_as_parameters() -> None:
    client = FakeClient(_FULL)

    _ = fetch_latest(client, ("temperature",), _WINDOW)

    sql, parameters = client.calls[-1]
    assert "$since" in sql
    assert parameters["since"] == _WINDOW.since.isoformat()
    assert "2026-08-01" not in sql


def test_latest_binds_requested_sensors_as_parameters() -> None:
    client = FakeClient(_FULL)

    _ = fetch_latest(client, ("temperature",), _WINDOW, ["cryo-77k", "room-1"])

    sql, parameters = client.calls[-1]
    assert "cryo-77k" not in sql
    assert set(parameters.values()) >= {"cryo-77k", "room-1"}


def test_a_table_without_a_sensor_id_is_left_out_rather_than_failing() -> None:
    # A measurement written by something else may have no sensor_id at
    # all. "The latest per sensor" is meaningless there, and refusing the
    # whole command because one table is unusual would be worse.
    client = FakeClient(("time", "value"))

    _ = fetch_latest(client, ("temperature",), _WINDOW)

    selects = [sql for sql, _ in client.calls if sql.startswith("SELECT '")]
    assert selects == []


class UnevenClient(FakeClient):
    """Answers with a different column set per table, as a real server does.

    A measurement written by a calibrated sensor has `input_volts` and
    `calibration_id`; a simulated one has neither. Every arm of a UNION
    must still project the same columns or the server refuses to plan it.
    """

    def __init__(self, per_table: dict[str, tuple[str, ...]]) -> None:
        super().__init__()
        self._per_table: dict[str, tuple[str, ...]] = per_table
        self._asked: list[str] = []

    @override
    def query(self, query: str, *args: object, **kwargs: object) -> pa.Table:
        if "information_schema.columns" in query:
            raw = kwargs.get("query_parameters")
            parameters = cast(dict[str, str], raw) if isinstance(raw, dict) else {}
            table = parameters.get("table", "")
            self._asked.append(table)
            self.calls.append((query, dict(parameters)))
            return pa.table({"column_name": pa.array(list(self._per_table[table]))})
        return super().query(query, *args, **kwargs)  # pyright: ignore[reportArgumentType]


def test_every_union_arm_projects_the_same_columns() -> None:
    # The server refuses to plan a UNION whose arms differ in width, so a
    # calibrated table and a simulated one cannot each select only what
    # they happen to have.
    client = UnevenClient(
        {
            "temperature": _FULL,
            "pressure": ("time", "value", "sensor_id", "unit"),
        }
    )

    _ = fetch_latest(client, ("temperature", "pressure"), _WINDOW)

    sql = client.calls[-1][0]
    arms = sql.split("UNION ALL")
    # Counting aliases rather than the word AS: `CAST(NULL AS VARCHAR)`
    # carries one of its own, so a bare count says the arms differ when
    # their projections match exactly.
    widths = {arm.count('AS "') for arm in arms}
    assert len(widths) == 1, f"arms differ in width: {widths}"


def test_a_missing_column_is_selected_as_null() -> None:
    client = UnevenClient(
        {
            "temperature": _FULL,
            "pressure": ("time", "value", "sensor_id", "unit"),
        }
    )

    _ = fetch_latest(client, ("temperature", "pressure"), _WINDOW)

    pressure_arm = client.calls[-1][0].split("UNION ALL")[1]
    assert "NULL" in pressure_arm
    assert '"input_volts"' in pressure_arm


def test_latest_of_no_usable_measurements_is_empty() -> None:
    client = FakeClient(("time", "value"))

    result = fetch_latest(client, ("temperature",), _WINDOW)

    assert result.num_rows == 0


# --------------------------------------------------------------------------
# Window statistics, in the same grouping
# --------------------------------------------------------------------------


def test_statistics_are_not_computed_unless_asked_for() -> None:
    client = FakeClient(_FULL)

    _ = fetch_latest(client, ("temperature",), _WINDOW)

    sql = client.calls[-1][0]
    assert "avg(" not in sql
    assert "stddev(" not in sql


def test_statistics_ride_along_in_the_same_grouping() -> None:
    # The aggregates run over rows `last_value` is already reading, so
    # they cost nothing extra and cannot describe a different window
    # from the value they sit beside.
    client = FakeClient(_FULL)

    _ = fetch_latest(client, ("temperature",), _WINDOW, stats=True)

    selects = [sql for sql, _ in client.calls if sql.startswith("SELECT '")]
    assert len(selects) == 1
    assert 'avg("value") AS "mean"' in selects[0]
    assert 'stddev("value") AS "sd"' in selects[0]
    assert 'count("value") AS "n"' in selects[0]
    assert selects[0].count('GROUP BY "sensor_id"') == 1


def test_statistics_are_projected_by_every_arm() -> None:
    # A UNION arm that lacked them would not compile; more usefully, a
    # measurement missing from the statistics would read as a sensor
    # with no history rather than as a bug.
    client = FakeClient(_FULL)

    _ = fetch_latest(client, ("temperature", "pressure"), _WINDOW, stats=True)

    arms = client.calls[-1][0].split("UNION ALL")
    assert len(arms) == 2
    for arm in arms:
        assert 'AS "mean"' in arm
        assert 'AS "sd"' in arm
        assert 'AS "n"' in arm


def test_an_empty_statistics_result_still_has_the_columns() -> None:
    # The renderer decides what to show by which columns are present, so
    # an empty result that dropped them would silently change the view.
    client = FakeClient(("time", "value"))

    result = fetch_latest(client, ("temperature",), _WINDOW, stats=True)

    assert result.num_rows == 0
    assert {"mean", "sd", "n"} <= set(result.column_names)


def test_an_empty_result_without_statistics_does_not_invent_them() -> None:
    client = FakeClient(("time", "value"))

    result = fetch_latest(client, ("temperature",), _WINDOW)

    assert not {"mean", "sd", "n"} & set(result.column_names)
