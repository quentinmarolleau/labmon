"""Minimal local stub for the parts of asteval labmon actually uses.

asteval ships no type information, so basedpyright's strictest preset
can't see through it. Same fix as the influxdb_client_3 and pint stubs
next door.
"""

class _AstevalError:
    def get_error(self) -> tuple[str, str]: ...

class Interpreter:
    symtable: dict[str, object]
    error: list[_AstevalError]

    def __init__(self) -> None: ...
    def __call__(self, expression: str, /) -> object: ...
