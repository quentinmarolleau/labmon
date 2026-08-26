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
