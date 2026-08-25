"""Invoking the CLI in a test, with the styling taken back off.

Rich forces colour on when `GITHUB_ACTIONS` is set, and its highlighter
splits a flag across two spans — `--port` arrives as `-` followed by
`-port`, styled separately. A substring search for a flag name then
finds nothing on CI while passing locally, where the runner's pipe is
not a terminal and Rich writes plain text. Setting `NO_COLOR` does not
help; the CI detection wins. Stripping the escapes here means an
assertion reads what a person would see, on either machine.

Not named `test_*`, so pytest does not collect it.
"""

import re
from dataclasses import dataclass

import typer
from typer.testing import CliRunner

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_runner = CliRunner()


@dataclass(frozen=True)
class Invocation:
    """What a command printed and how it ended, free of escape codes."""

    exit_code: int
    output: str
    stdout: str
    stderr: str


def invoke(app: typer.Typer, args: list[str]) -> Invocation:
    """Run `app` with `args` and return its unstyled result."""
    result = _runner.invoke(app, args)
    return Invocation(
        exit_code=result.exit_code,
        output=_ANSI.sub("", result.output),
        stdout=_ANSI.sub("", result.stdout),
        stderr=_ANSI.sub("", result.stderr),
    )
