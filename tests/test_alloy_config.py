"""The collectors' wiring, pinned where a reformat would not notice.

Neither `alloy/config.alloy` nor `alloy/client.alloy` is exercised by
anything else here: they run inside a container, against the Docker API,
in a profile the test suite does not start. These check the few
properties that would fail silently — a source wired straight past the
processing stage still collects logs, it just stops labelling them, and
nothing complains.

Pinning the text cannot tell a config that ships lines from one that
parses perfectly and ships none. For the client that gap is closed by
the smoke job, which runs `client.alloy` against a live stack and
asserts its lines arrive labelled; the server's is covered by
`scripts/smoke_logs.py`.
"""

import re
from pathlib import Path

import pytest

_ALLOY = Path(__file__).resolve().parent.parent / "alloy"

_SERVER = _ALLOY / "config.alloy"
_CLIENT = _ALLOY / "client.alloy"

# Each config's own destination. The server writes to the Loki beside it;
# a client writes to the proxy in front of the server's, which is a
# `loki.write` under a different name rather than a different mechanism.
_WRITES_TO = {
    _SERVER: "loki.write.local.receiver",
    _CLIENT: "loki.write.server.receiver",
}

_SOURCES = ('loki.source.docker "containers"', 'loki.source.journal "units"')

# Both files collect the same two sources through the same processing
# stage, so the wiring checks below hold for either.
_EITHER = pytest.mark.parametrize(
    "config", [_SERVER, _CLIENT], ids=["server", "client"]
)


def _block(config: Path, name: str) -> str:
    """The body of one top-level block, by its `type "label"` header."""
    text = config.read_text(encoding="utf-8")
    start = text.index(f"{name} {{")
    depth = 0
    for offset in range(start, len(text)):
        if text[offset] == "{":
            depth += 1
        elif text[offset] == "}":
            depth -= 1
            if depth == 0:
                return text[start : offset + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _rules(body: str) -> list[str]:
    """The bodies of every `rule { ... }` in a relabel block."""
    return re.findall(r"rule\s*\{([^}]*)\}", body)


@_EITHER
def test_both_sources_forward_through_the_processing_stage(config: Path) -> None:
    """Docker and journal alike, or one shape of deployment loses labels.

    A source pointed straight at `loki.write` keeps working — the lines
    arrive, unlabelled — so this is the failure that survives review.
    """
    for source in _SOURCES:
        body = _block(config, source)

        assert "loki.process.sensors.receiver" in body, (
            f"{source} does not forward through loki.process.sensors, so its "
            "lines reach Loki without a sensor_id label"
        )


@_EITHER
def test_the_processing_stage_reaches_its_destination(config: Path) -> None:
    body = _block(config, 'loki.process "sensors"')

    assert _WRITES_TO[config] in body


@_EITHER
def test_sensor_id_is_read_from_the_line_and_promoted_to_a_label(config: Path) -> None:
    """Extraction alone is invisible: `stage.labels` is what publishes it."""
    body = _block(config, 'loki.process "sensors"')

    assert re.search(r"stage\.logfmt\s*\{[^}]*\"sensor_id\"", body), (
        "no logfmt stage extracting sensor_id"
    )
    assert re.search(r"stage\.labels\s*\{[^}]*\"sensor_id\"", body), (
        "sensor_id is extracted but never promoted to a label"
    )


@_EITHER
def test_no_source_writes_to_loki_directly(config: Path) -> None:
    """The stage is only load-bearing while everything goes through it."""
    for source in _SOURCES:
        assert _WRITES_TO[config] not in _block(config, source)


@_EITHER
def test_only_labmon_units_are_collected_from_the_journal(config: Path) -> None:
    """Widening this ships the whole machine's journal, and looks fine.

    The host journal is every login session and system service on the
    box. Nothing downstream would report a problem — Loki would simply
    fill with logs nobody asked to centralise, which on a client is
    somebody's own lab machine.
    """
    keeps = [
        rule
        for rule in _rules(_block(config, 'discovery.relabel "journal"'))
        if '"keep"' in rule
    ]

    assert len(keeps) == 1, "expected exactly one keep rule bounding the journal"
    assert '"unit"' in keeps[0], "the keep rule does not match on the unit name"
    assert '"labmon-.*"' in keeps[0], (
        "the journal is no longer restricted to labmon's own units"
    )


@_EITHER
def test_the_journal_source_finds_both_journal_layouts(config: Path) -> None:
    """Pinning `path` collects nothing on a volatile-journal host.

    Left empty, Alloy reads `/var/log/journal` and `/run/log/journal`
    both. Setting it to the persistent one alone still works on most
    machines, so the failure only appears on a host with
    `Storage=volatile` — where container logs keep arriving and the
    systemd sensor's silently do not.
    """
    body = _block(config, 'loki.source.journal "units"')

    assert not re.search(r"^\s*path\s*=", body, re.MULTILINE), (
        "the journal source pins a path, so only that directory is read"
    )


def test_a_client_stamps_its_own_name_on_every_line() -> None:
    """Without `host`, two clients are one stream and cannot be separated.

    The label is only added client-side, so the server's config has no
    equivalent to check: its lines are the ones with no `host` at all.
    """
    body = _block(_CLIENT, 'loki.process "sensors"')

    assert re.search(
        r"stage\.static_labels\s*\{[^}]*\"host\"\s*=\s*sys\.env\(\"LABMON_CLIENT_NAME\"\)",
        body,
    ), "a client's lines carry no host label naming the machine they came from"


def test_a_client_reads_its_credentials_from_the_environment() -> None:
    """A literal here would work, and would be committed.

    Nothing about the push would look wrong — which is the point of
    pinning it rather than trusting review to catch it.
    """
    body = _block(_CLIENT, 'loki.write "server"')

    for setting in ("username", "password", "ca_file"):
        assert re.search(rf"{setting}\s*=\s*sys\.env\(", body), (
            f"{setting} is not read from the environment"
        )
