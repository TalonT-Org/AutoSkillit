"""Tests for _io module utilities."""

from __future__ import annotations


# IU1
def test_io_module_has_docstring():
    import autoskillit._io as _io_mod

    assert _io_mod.__doc__ is not None and len(_io_mod.__doc__.strip()) > 0
