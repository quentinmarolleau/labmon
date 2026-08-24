"""pyarrow.ipc — the Feather V2 / Arrow IPC file writer."""

from pathlib import Path
from typing import BinaryIO

from pyarrow import RecordBatchFileWriter, Schema, Table

class IpcWriteOptions:
    def __init__(self, *, compression: str | None = ...) -> None: ...

def new_file(
    sink: str | Path | BinaryIO,
    schema: Schema,
    *,
    options: IpcWriteOptions | None = ...,
) -> RecordBatchFileWriter: ...
def open_file(source: str | Path | BinaryIO) -> RecordBatchFileReader: ...

class RecordBatchFileReader:
    def read_all(self) -> Table: ...
