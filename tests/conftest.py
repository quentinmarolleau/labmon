"""Fixtures every test in the suite gets.

Kept to isolation rather than convenience: a test that reaches outside
the repository is a test that changes the machine it runs on.
"""

from pathlib import Path

import pytest


# An autouse fixture is called by pytest, never by name, so a checker
# reasonably reports it as unused.
@pytest.fixture(autouse=True)
def _isolated_cache(  # pyright: ignore[reportUnusedFunction]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the roster cache inside the test's own directory.

    `labmon query --latest` remembers the sensors it saw, and without
    this the suite writes that file into the home directory of whoever
    runs it — inventing sensors from fixtures, and carrying them between
    runs. Applied to every test rather than only the ones known to need
    it, because the next command to reach for the cache should not have
    to remember.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


@pytest.fixture(autouse=True)
def _isolated_config(  # pyright: ignore[reportUnusedFunction]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the configuration file inside the test's own directory.

    Every command reads it, so without this the suite's behaviour
    depends on whether whoever runs it has set a timezone — and a
    passing run on one machine says nothing about another.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@pytest.fixture(autouse=True)
def _isolated_working_directory(  # pyright: ignore[reportUnusedFunction]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run each test somewhere that is not the repository.

    Every command reads `./.env` now, and the repository root has a real
    one on any machine the stack has been started on. Without this the
    suite would load a developer's own token and host into `os.environ`
    and pass or fail accordingly — the sharpest form of the problem this
    file exists to prevent, since it would also differ between a
    contributor's checkout and CI.

    It doubles as protection for the other direction: a command that
    writes a file relative to the working directory now writes it into
    the test's own directory rather than into the tree.
    """
    monkeypatch.chdir(tmp_path)
