"""The collector's wiring, pinned where a reformat would not notice.

`alloy/config.alloy` is not exercised by anything else here: it runs
inside a container, against the Docker API, in a profile the test suite
does not start. These check the few properties that would fail silently
— a source wired straight past the processing stage still collects logs,
it just stops labelling them, and nothing complains.
"""

import re
from pathlib import Path

_CONFIG = Path(__file__).resolve().parent.parent / "alloy" / "config.alloy"


def _config() -> str:
    return _CONFIG.read_text(encoding="utf-8")


def _block(name: str) -> str:
    """The body of one top-level block, by its `type "label"` header."""
    text = _config()
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


def test_both_sources_forward_through_the_processing_stage() -> None:
    """Docker and journal alike, or one shape of deployment loses labels.

    A source pointed straight at `loki.write` keeps working — the lines
    arrive, unlabelled — so this is the failure that survives review.
    """
    for source in ('loki.source.docker "containers"', 'loki.source.journal "units"'):
        body = _block(source)

        assert "loki.process.sensors.receiver" in body, (
            f"{source} does not forward through loki.process.sensors, so its "
            "lines reach Loki without a sensor_id label"
        )


def test_the_processing_stage_reaches_loki() -> None:
    body = _block('loki.process "sensors"')

    assert "loki.write.local.receiver" in body


def test_sensor_id_is_read_from_the_line_and_promoted_to_a_label() -> None:
    """Extraction alone is invisible: `stage.labels` is what publishes it."""
    body = _block('loki.process "sensors"')

    assert re.search(r"stage\.logfmt\s*\{[^}]*\"sensor_id\"", body), (
        "no logfmt stage extracting sensor_id"
    )
    assert re.search(r"stage\.labels\s*\{[^}]*\"sensor_id\"", body), (
        "sensor_id is extracted but never promoted to a label"
    )


def test_no_source_writes_to_loki_directly() -> None:
    """The stage is only load-bearing while everything goes through it."""
    for source in ('loki.source.docker "containers"', 'loki.source.journal "units"'):
        assert "loki.write.local.receiver" not in _block(source)
