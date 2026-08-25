"""pyarrow.parquet, the read/write calls labmon and its tests use."""

from pathlib import Path
from typing import BinaryIO

from pyarrow import Schema, Table

def write_table(
    table: Table,
    where: str | Path | BinaryIO,
    compression: str = ...,
    store_schema: bool = ...,
) -> None: ...
def read_table(source: str | Path | BinaryIO) -> Table: ...
def read_schema(where: str | Path | BinaryIO) -> Schema: ...
