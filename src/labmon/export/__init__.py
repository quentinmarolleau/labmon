"""Reading recorded readings back out, and writing them to files.

The command line that drives this lives in `labmon.cli`; everything here
is library code with no command line in it.

Deliberately empty of imports. Python runs a package's `__init__` when
any submodule is imported, so re-exporting the convenient names here
would mean `from labmon.export.formats import SUFFIXES` — which the
command line does just to build `--format`'s choices — loading pyarrow
and everything under it. Import from the submodule that owns the name:

    from labmon.export.formats import FORMATS, SUFFIXES, ExportError
    from labmon.export.table import EXPORT_COLUMNS, combine, normalise
    from labmon.export.window import Window, WindowError
    from labmon.export.writers import write
"""
