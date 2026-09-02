"""What labmon reports as its own version, and the flag that asks.

The number itself is not asserted — it comes from `pyproject.toml` and
would make this a second place to bump. What is asserted is that the
lookup agrees with installed metadata, that a tree with no metadata
degrades rather than raising, and that `--version` reaches the same
answer through the CLI.
"""

from importlib import metadata

import pytest

from labmon import version
from labmon.cli.main import build_app
from tests.cli_runner import invoke


def test_the_version_is_the_installed_distributions() -> None:
    assert version.installed_version() == metadata.version("labmon")


def test_a_tree_with_no_metadata_reports_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running off PYTHONPATH with nothing installed is not an error.

    `labmon.export.table` stamps this into every exported file, so a
    raise here would fail an export over a fact nobody asked for.
    """

    def _missing(_name: str) -> str:
        raise metadata.PackageNotFoundError(_name)

    # `labmon.version` holds the same module object, so patching the
    # attribute here is what its call sees.
    monkeypatch.setattr(metadata, "version", _missing)
    assert version.installed_version() == version.UNKNOWN


def test_the_flag_prints_the_version() -> None:
    result = invoke(build_app(), ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"labmon {metadata.version('labmon')}"


def test_the_root_callback_does_not_swallow_a_subcommand() -> None:
    """The callback carrying `--version` runs before every subcommand.

    A callback that raised or consumed the arguments would break all of
    them at once, and nothing else in the suite would say why.
    """
    result = invoke(build_app(), ["sensors", "--help"])
    assert result.exit_code == 0
    assert "sensors" in result.output


def test_no_arguments_still_prints_the_help() -> None:
    result = invoke(build_app(), [])
    assert result.exit_code != 0
    assert "Usage" in result.output
