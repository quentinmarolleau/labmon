"""Structural checks on the provisioned Grafana dashboard.

`grafana/dashboards/lab-overview.json` is a thousand lines of hand-edited
JSON that Grafana loads at boot. Grafana is forgiving with it in the worst
way: a panel whose query landed under the wrong key, or that points at a
datasource uid nobody provisioned, renders "No data" and logs nothing. The
dashboard looks fine until someone reads a number that isn't there.

None of this validates the dashboard against Grafana's schema — there is no
published schema to validate against. Each check below stands for a mistake
that has actually been made in this file.
"""

import json
import re
from pathlib import Path
from typing import cast

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD = _REPO_ROOT / "grafana" / "dashboards" / "lab-overview.json"
_DATASOURCE = _REPO_ROOT / "grafana" / "provisioning" / "datasources" / "influxdb3.yaml"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"

# `$channel`, `${channel}`, and Grafana's own `$__timeFrom()` macros, which
# are built in rather than declared by the dashboard.
_VARIABLE_REFERENCE = re.compile(r"\$\{?(\w+)\}?")
_BUILTIN_PREFIX = "__"


def _parse(path: Path) -> object:
    """`json.loads` is typed as returning `Any`; nothing here wants that."""
    return json.loads(path.read_text(encoding="utf-8"))  # pyright: ignore[reportAny]


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), f"expected an object, got {type(value).__name__}"
    return cast(dict[str, object], value)


def _mappings(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list), f"expected a list, got {type(value).__name__}"
    return [_mapping(item) for item in cast(list[object], value)]


@pytest.fixture(scope="module")
def dashboard() -> dict[str, object]:
    """The dashboard as Grafana will parse it.

    Doubles as the "it is still valid JSON" check: every test in this module
    depends on this fixture, so a syntax error fails all of them at once
    rather than surfacing as a confusing assertion elsewhere.
    """
    return _mapping(_parse(_DASHBOARD))


@pytest.fixture(scope="module")
def panels(dashboard: dict[str, object]) -> list[dict[str, object]]:
    return _mappings(dashboard["panels"])


@pytest.fixture(scope="module")
def targets(panels: list[dict[str, object]]) -> list[tuple[str, dict[str, object]]]:
    """Every query in the dashboard, tagged with its panel title."""
    return [
        (str(panel["title"]), target)
        for panel in panels
        for target in _mappings(panel.get("targets", []))
    ]


def test_every_target_carries_a_non_empty_raw_sql(
    targets: list[tuple[str, dict[str, object]]],
) -> None:
    """The InfluxDB datasource in SQL mode reads `rawSql` and only `rawSql`.

    A query written under `query` instead is accepted by the JSON, sent to
    the backend empty, and comes back as a 400 the panel never shows.
    """
    for title, target in targets:
        raw_sql = target.get("rawSql")
        assert isinstance(raw_sql, str) and raw_sql.strip(), (
            f"{title!r} target {target.get('refId')!r} has no rawSql"
        )


def test_no_target_keeps_a_duplicate_query_field(
    targets: list[tuple[str, dict[str, object]]],
) -> None:
    """`query` alongside `rawSql` is dead weight that drifts out of sync.

    Grafana's UI writes both when a dashboard is edited in the browser and
    exported. Only one of them is read, so the other quietly becomes a
    second, wrong copy of the query for anyone reading the file.
    """
    for title, target in targets:
        assert "query" not in target, (
            f"{title!r} target {target.get('refId')!r} still carries a `query` "
            "field; `rawSql` is the one that is read"
        )


def test_targets_point_at_the_provisioned_datasource(
    targets: list[tuple[str, dict[str, object]]],
) -> None:
    """A uid nobody provisioned fails at render time, not at load time.

    The uid is pulled out of the provisioning file with a regex rather than
    a YAML parser: this is the only YAML the test suite reads, and one
    `uid:` line does not justify a dependency.
    """
    provisioned = re.search(
        r"^\s*uid:\s*(\S+)\s*$", _DATASOURCE.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert provisioned is not None, f"no `uid:` found in {_DATASOURCE.name}"

    for title, target in targets:
        datasource = _mapping(target["datasource"])
        assert datasource.get("uid") == provisioned.group(1), (
            f"{title!r} points at datasource uid {datasource.get('uid')!r}, "
            f"but {_DATASOURCE.name} provisions {provisioned.group(1)!r}"
        )
        assert datasource.get("type") == "influxdb"


def test_panel_ids_are_unique(panels: list[dict[str, object]]) -> None:
    """Grafana keys panel state by id; duplicates interfere silently."""
    ids = [panel["id"] for panel in panels]
    assert len(ids) == len(set(map(str, ids))), f"duplicate panel ids in {ids}"


def test_every_referenced_variable_is_declared(
    dashboard: dict[str, object], panels: list[dict[str, object]]
) -> None:
    """An undeclared `$name` interpolates to nothing.

    The result is usually still valid SQL — `WHERE sensor_id = ''` — so the
    query succeeds and returns no rows, which looks exactly like a sensor
    that stopped reporting.
    """
    declared = {
        str(variable["name"])
        for variable in _mappings(_mapping(dashboard["templating"])["list"])
    }

    for panel in panels:
        text = " ".join(
            [str(panel.get("title", ""))]
            + [str(target.get("rawSql", "")) for target in _mappings(panel["targets"])]
        )
        names = cast(list[str], _VARIABLE_REFERENCE.findall(text))
        referenced = {name for name in names if not name.startswith(_BUILTIN_PREFIX)}
        undeclared = referenced - declared
        assert not undeclared, (
            f"{panel.get('title')!r} references undeclared variable(s) "
            f"{sorted(undeclared)}; declared: {sorted(declared)}"
        )


def test_custom_variables_list_their_values_inline(
    dashboard: dict[str, object],
) -> None:
    """A custom variable's values come from `query`, never from `options`.

    Grafana rebuilds `options` from the comma-separated `query` on load, so
    an exported dashboard's `options` array is already stale by the time it
    is committed. Populating one and leaving `query` empty yields a variable
    with no values at all.
    """
    for variable in _mappings(_mapping(dashboard["templating"])["list"]):
        if variable.get("type") != "custom":
            continue
        query = variable.get("query")
        assert isinstance(query, str) and query.strip(), (
            f"custom variable {variable.get('name')!r} has no inline `query`"
        )


def test_xychart_panels_declare_a_plugin_version(
    panels: list[dict[str, object]],
) -> None:
    """Without one, Grafana treats the panel as pre-11.1 and migrates it.

    The migration expects `series[].x` to be a plain field name rather than
    a matcher, produces zero series, and renders "No data" with no error
    logged anywhere.
    """
    for panel in panels:
        if panel.get("type") != "xychart":
            continue
        version = panel.get("pluginVersion")
        assert isinstance(version, str) and version.strip(), (
            f"xychart panel {panel.get('title')!r} needs an explicit "
            "pluginVersion or Grafana will migrate it into emptiness"
        )


def test_third_party_panel_types_are_installed_by_the_demo(
    panels: list[dict[str, object]],
) -> None:
    """A panel plugin nobody installs renders as "plugin not found".

    Core panel types are single words (`stat`, `table`, `xychart`); plugin
    ids are vendor-prefixed and hyphenated, which is what separates the two
    here.
    """
    requested = re.search(
        r"^GRAFANA_PLUGINS=(.*)$",
        _ENV_EXAMPLE.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert requested is not None, f"no GRAFANA_PLUGINS line in {_ENV_EXAMPLE.name}"
    installed = {
        entry.split("@", 1)[0].strip()
        for entry in requested.group(1).split(",")
        if entry.strip()
    }

    for panel in panels:
        panel_type = str(panel["type"])
        if "-" not in panel_type:
            continue
        assert panel_type in installed, (
            f"{panel.get('title')!r} uses panel plugin {panel_type!r}, which "
            f"GRAFANA_PLUGINS does not install (it installs {sorted(installed)})"
        )
