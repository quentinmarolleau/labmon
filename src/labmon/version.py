"""What version of labmon is installed.

Read back from the installed distribution's metadata rather than kept as
a constant in the source. The version is declared once, in
`pyproject.toml`, and a second copy here would be a second place to bump
and the first place to forget.
"""

from importlib import metadata

#: The distribution name, which is also the import name.
DISTRIBUTION = "labmon"

#: Reported when the metadata is not there to read.
UNKNOWN = "unknown"


def installed_version() -> str:
    """The installed version, or `UNKNOWN`.

    A checkout that was never installed has no metadata to answer from —
    running the package straight off `PYTHONPATH`, say — and that is not
    an error worth failing an export or a `--version` over.
    """
    try:
        return metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return UNKNOWN
