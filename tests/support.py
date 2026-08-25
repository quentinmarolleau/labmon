"""Typed wrappers around the parts of xarray that ship loose signatures.

`open_dataset` and `attrs` come back partly unknown, and left alone that
leaks through every assertion that touches them. Contained here so the
tests themselves stay fully checked.

Not named `test_*`, so pytest does not collect it.
"""

from pathlib import Path

import xarray as xr


def open_dataset(path: Path, *, decode_times: bool = True) -> xr.Dataset:
    """Open a netCDF file with a typed result."""
    return xr.open_dataset(  # pyright: ignore[reportUnknownMemberType]
        path, decode_times=decode_times
    )


def attr(node: xr.DataArray, name: str) -> str:
    """One variable attribute as text; `attrs` is untyped upstream."""
    return str(node.attrs[name])  # pyright: ignore[reportAny]


def has_attr(node: xr.DataArray, name: str) -> bool:
    """Whether a variable carries `name` at all."""
    return name in node.attrs
