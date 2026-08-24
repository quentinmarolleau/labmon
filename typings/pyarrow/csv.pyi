"""pyarrow.csv, write side only."""

from typing import BinaryIO

from pyarrow import Table

def write_csv(data: Table, output_file: BinaryIO) -> None: ...
