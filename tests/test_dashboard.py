"""Structural checks on every provisioned Grafana dashboard.

`grafana/dashboards/` holds hand-edited JSON that Grafana loads at boot.
Grafana is forgiving with it in the worst way: a panel whose query landed
under the wrong key, or that points at a datasource uid nobody provisioned,
renders "No data" and logs nothing. The dashboard looks fine until someone
reads a number that isn't there.

Every check runs against every dashboard in that directory, so adding one
needs no change here. That generality is the point: the suite was written
around `lab-overview.json` by name, and a second dashboard on a different
datasource could not be added without first teaching it that a dashboard is
not necessarily an InfluxDB dashboard.

None of this validates the dashboards against Grafana's schema — there is no
published schema to validate against — and none of it runs the queries, so a
`rawSql` naming a column that does not exist passes every check here;
catching that needs a live stack. Each check below stands for a mistake that
has actually been made in one of these files.
"""

import json
import re
from pathlib import Path
from typing import cast

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "grafana" / "dashboards"
_DATASOURCE_DIR = _REPO_ROOT / "grafana" / "provisioning" / "datasources"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"

# The key each datasource type reads its query from. A query written under
# any other name is accepted by the JSON, sent to the backend empty, and
# comes back as an error the panel never shows — which is the single
# mistake this file exists to catch, and it is spelled differently per
# backend. A type missing from here fails the check rather than skipping
# it, so adding a datasource cannot silently disable the check for it.
_QUERY_FIELD = {"influxdb": "rawSql", "loki": "expr"}

# `$channel`, `${channel}`, and Grafana's own `$__timeFrom()` macros, which
# are built in rather than declared by the dashboard.
_VARIABLE_REFERENCE = re.compile(r"\$\{?(\w+)\}?")
_BUILTIN_PREFIX = "__"

# Layout-only panel types. Everything else on this dashboard draws data and
# is therefore useless without at least one target.
_PANEL_TYPES_WITHOUT_QUERIES = frozenset({"row", "text"})


def _parse(path: Path) -> object:
    """`json.loads` is typed as returning `Any`; nothing here wants that."""
    return json.loads(path.read_text(encoding="utf-8"))  # pyright: ignore[reportAny]


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), f"expected an object, got {type(value).__name__}"
    return cast(dict[str, object], value)


def _mappings(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list), f"expected a list, got {type(value).__name__}"
    return [_mapping(item) for item in cast(list[object], value)]


def _dashboard_paths() -> list[Path]:
    """Every dashboard Grafana will provision, in a stable order."""
    return sorted(_DASHBOARD_DIR.glob("*.json"))


def _provisioned_datasources() -> dict[str, str]:
    """uid to type, read from every datasource provisioning file.

    Parsed with a regex rather than a YAML library. This is still the only
    YAML the test suite reads, and the alternative is a dependency carried
    solely to look up two keys per entry.

    The regex pins `type:` to the indentation of its own entry's `uid:`,
    so a `type` nested under `jsonData` cannot be mistaken for the
    datasource's own — which a looser pattern would do the moment someone
    provisions a datasource whose options happen to include one.
    """
    found: dict[str, str] = {}
    for path in sorted(_DATASOURCE_DIR.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        # Split on the list marker so each uid pairs with the type from its
        # own entry rather than a neighbour's.
        entries = re.split(r"^\s*-\s+(?=name:)", text, flags=re.MULTILINE)[1:]
        assert entries, f"no datasource entries found in {path.name}"
        for entry in entries:
            uid = re.search(
                r"^(?P<indent>[ ]*)uid:\s*(?P<uid>\S+)\s*$", entry, re.MULTILINE
            )
            assert uid is not None, f"a datasource in {path.name} declares no `uid:`"
            kind = re.search(
                rf"^{uid.group('indent')}type:\s*(\S+)\s*$", entry, re.MULTILINE
            )
            assert kind is not None, (
                f"datasource {uid.group('uid')!r} in {path.name} declares no `type:`"
            )
            found[uid.group("uid")] = kind.group(1)
    return found


def test_at_least_one_dashboard_is_provisioned() -> None:
    """Guards the glob itself.

    Every other check is parametrised over the dashboards it finds, so a
    directory rename would turn this whole module green by collecting
    nothing at all.
    """
    assert _dashboard_paths(), f"no dashboards found in {_DASHBOARD_DIR}"


def _dashboard_id(path: Path) -> str:
    """Names each parametrised run after its file, so a failure says which."""
    return path.stem


@pytest.fixture(scope="module", params=_dashboard_paths(), ids=_dashboard_id)
def dashboard(request: pytest.FixtureRequest) -> dict[str, object]:
    """One dashboard as Grafana will parse it, once per file.

    Doubles as the "it is still valid JSON" check: every test in this module
    depends on this fixture, so a syntax error fails all of them at once
    rather than surfacing as a confusing assertion elsewhere.
    """
    return _mapping(_parse(cast(Path, request.param)))


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


def test_query_panels_declare_at_least_one_target(
    panels: list[dict[str, object]],
) -> None:
    """A data panel with no targets renders empty, exactly like a bad query.

    Grafana drops `targets` on export when a panel's last query is deleted in
    the browser, so this is a plausible way to lose one by accident.
    """
    for panel in panels:
        if str(panel.get("type")) in _PANEL_TYPES_WITHOUT_QUERIES:
            continue
        assert _mappings(panel.get("targets", [])), (
            f"{panel.get('title')!r} is a {panel.get('type')!r} panel with no "
            "targets; it can only ever render empty"
        )


def test_every_target_carries_a_non_empty_query(
    targets: list[tuple[str, dict[str, object]]],
) -> None:
    """Each backend reads its query from one key and ignores the rest.

    InfluxDB in SQL mode reads `rawSql`; Loki reads `expr`. A query written
    under any other name is accepted by the JSON, sent to the backend
    empty, and comes back as an error the panel never shows.
    """
    for title, target in targets:
        datasource = _mapping(target.get("datasource", {}))
        kind = str(datasource.get("type"))
        field = _QUERY_FIELD.get(kind)
        assert field is not None, (
            f"{title!r} targets a {kind!r} datasource, which this suite does "
            f"not know the query field for; add it to _QUERY_FIELD "
            f"(known: {sorted(_QUERY_FIELD)})"
        )
        query = target.get(field)
        assert isinstance(query, str) and query.strip(), (
            f"{title!r} target {target.get('refId')!r} has no {field}"
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


def test_targets_point_at_a_provisioned_datasource(
    targets: list[tuple[str, dict[str, object]]],
) -> None:
    """A uid nobody provisions fails at render time, not at load time.

    Any provisioned datasource will do — a dashboard is not required to be
    an InfluxDB one — but the reference has to resolve, and its declared
    `type` has to be the type that uid actually provisions. A panel
    claiming `influxdb` against Loki's uid renders empty just as reliably
    as one naming a uid that does not exist.
    """
    provisioned = _provisioned_datasources()

    for title, target in targets:
        # A target may legitimately omit `datasource` and inherit the panel's,
        # so report the absence as a mismatch rather than dying on a KeyError.
        datasource = _mapping(target.get("datasource", {}))
        uid = datasource.get("uid")
        assert uid in provisioned, (
            f"{title!r} points at datasource uid {uid!r}, which nothing in "
            f"{_DATASOURCE_DIR.name}/ provisions (it provisions "
            f"{sorted(provisioned)})"
        )
        assert datasource.get("type") == provisioned[str(uid)], (
            f"{title!r} calls uid {uid!r} a {datasource.get('type')!r} "
            f"datasource, but it is provisioned as {provisioned[str(uid)]!r}"
        )


def test_every_datasource_a_dashboard_names_has_a_provisioning_file(
    dashboard: dict[str, object], panels: list[dict[str, object]]
) -> None:
    """Catches a dashboard that ships without the file that makes it render.

    Broader than the target check above: panels and template variables
    carry their own `datasource` blocks, and a variable pointing at a uid
    nobody provisions yields an empty dropdown, which then interpolates
    into every query on the dashboard.

    This matters most for the optional parts of the stack. Loki is only
    started under the `logs` profile, so a logs dashboard is exactly the
    kind of thing that could be committed while the datasource file that
    backs it is still sitting unstaged.
    """
    provisioned = _provisioned_datasources()
    variables = _mappings(_mapping(dashboard["templating"])["list"])

    named: list[tuple[str, object]] = [
        (f"panel {panel.get('title')!r}", panel["datasource"])
        for panel in panels
        if "datasource" in panel
    ]
    named.extend(
        (f"variable {variable.get('name')!r}", variable["datasource"])
        for variable in variables
        if "datasource" in variable
    )

    for where, reference in named:
        uid = _mapping(reference).get("uid")
        assert uid in provisioned, (
            f"{where} names datasource uid {uid!r}, which nothing in "
            f"{_DATASOURCE_DIR.name}/ provisions (it provisions "
            f"{sorted(provisioned)})"
        )


def test_panel_ids_are_unique(panels: list[dict[str, object]]) -> None:
    """Grafana keys panel state by id; duplicates interfere silently."""
    ids = [str(panel["id"]) for panel in panels]
    assert len(ids) == len(set(ids)), f"duplicate panel ids in {ids}"


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
        # `row` and `text` panels carry no targets at all, hence the default.
        panel_targets = _mappings(panel.get("targets", []))
        # Every backend's query key, not just InfluxDB's: a `$sensor` that
        # interpolates to nothing does so in LogQL exactly as it does in SQL.
        text = " ".join(
            [str(panel.get("title", ""))]
            + [
                str(target.get(field, ""))
                for target in panel_targets
                for field in _QUERY_FIELD.values()
            ]
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

    Community panel plugins are named `<org>-<name>-panel`, so the `-panel`
    suffix is what separates them from core types. Hyphenation alone is not:
    `state-timeline` and `status-history` ship with Grafana, and demanding
    they be added to GRAFANA_PLUGINS would make the plugin install fail at
    boot — a worse outcome than the one this check exists to prevent.
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
        if not panel_type.endswith("-panel"):
            continue
        assert panel_type in installed, (
            f"{panel.get('title')!r} uses panel plugin {panel_type!r}, which "
            f"GRAFANA_PLUGINS does not install (it installs {sorted(installed)})"
        )


# --------------------------------------------------------------------------
# the provisioning tree
# --------------------------------------------------------------------------

# Every directory Grafana looks for under /etc/grafana/provisioning. It
# reports a missing one at ERROR rather than DEBUG, so the two this
# deployment does not use still have to be there — otherwise they are two
# permanent false positives on the error dashboard, which is how a reader
# learns to ignore it.
_PROVISIONING_DIRS = ("alerting", "dashboards", "datasources", "plugins")


@pytest.mark.parametrize("name", _PROVISIONING_DIRS)
def test_grafana_reads_a_provisioning_directory_that_is_there(name: str) -> None:
    """Present, and not empty.

    Git tracks files rather than directories, so an empty one exists on
    the machine that made it and nowhere else. Asserting only that it is
    present would keep passing on a working copy where the directory
    lingers untracked, which is precisely the copy the author is looking
    at when they delete its placeholder.
    """
    directory = _REPO_ROOT / "grafana" / "provisioning" / name

    assert directory.is_dir(), (
        f"grafana/provisioning/{name} is missing; Grafana logs an ERROR on "
        "every boot for a provisioning directory it cannot read"
    )
    assert any(directory.iterdir()), (
        f"grafana/provisioning/{name} is empty, so it will not survive a "
        "fresh clone; commit a .gitkeep in it"
    )
