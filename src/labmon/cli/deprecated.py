"""The pre-`labmon` entry points, kept working and marked deprecated.

`mock-sensor` and `serial-sensor` shipped in v0.2.0-beta.1, so they are
in compose files, systemd units and shell history that labmon does not
control. They still work, and say once at startup what to use instead.

Scheduled for removal in 1.0.
"""

import logging

import typer

from labmon import logs
from labmon.cli.commands.mock_sensor import mock_sensor
from labmon.cli.commands.serial_sensor import serial_sensor
from labmon.cli.runtime import reporting

logger: logging.Logger = logging.getLogger(__name__)


def _announce(old: str, new: str) -> None:
    # Configured here so the notice is emitted in the same logfmt as
    # everything else; the command configures again with the requested
    # level, which is safe (logs.configure passes force=True).
    logs.configure()
    logger.warning(
        "this command is deprecated",
        extra={"command": old, "use": new, "removed_in": "1.0"},
    )


def mock_sensor_main() -> None:
    """Deprecated alias for `labmon mock-sensor`."""
    _announce("mock-sensor", "labmon mock-sensor")
    typer.run(reporting(mock_sensor))


def serial_sensor_main() -> None:
    """Deprecated alias for `labmon serial-sensor`."""
    _announce("serial-sensor", "labmon serial-sensor")
    typer.run(reporting(serial_sensor))
