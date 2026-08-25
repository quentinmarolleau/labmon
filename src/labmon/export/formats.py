"""What the export formats are called, and what can go wrong.

Separate from `labmon.export.writers` because these are needed to *build*
the command line — `--format`'s choices, the extension appended to
`--output` — while writing a file is what needs pyarrow. Keeping the
names here means `labmon --help` and tab completion do not pay 0.14s to
import a library they never call.
"""


class ExportError(RuntimeError):
    """A format that cannot do what was asked of it."""


# Extension each format gets when the CLI has to invent a filename.
SUFFIXES: dict[str, str] = {
    "csv": ".csv",
    "parquet": ".parquet",
    "feather": ".feather",
    "netcdf": ".nc",
}

FORMATS: tuple[str, ...] = tuple(SUFFIXES)

# Formats that can be written to a pipe. netCDF cannot: both engines seek
# while writing, which a stream does not support.
STREAMABLE: frozenset[str] = frozenset({"csv", "parquet", "feather"})
