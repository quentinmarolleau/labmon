"""Marks the test suite as a package.

Needed so `from tests.support import ...` resolves the same way for
pytest and for basedpyright; a bare `from support import ...` works
under pytest but reads as an implicit relative import to the type
checker.
"""
