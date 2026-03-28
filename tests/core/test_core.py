"""Tests for the core/ sub-package foundation layer."""

from __future__ import annotations


def test_core_io_module_has_docstring():
    import autoskillit.core.io as m

    assert m.__doc__ and "atomic" in m.__doc__


def test_dump_yaml_not_in_core_all():
    import autoskillit.core as core
    import autoskillit.core.io as core_io

    assert "dump_yaml" not in core.__all__
    assert "dump_yaml" not in core_io.__all__
    assert not hasattr(core_io, "dump_yaml")


def test_dump_yaml_not_in_io():
    """dump_yaml must be removed entirely from core.io."""
    import autoskillit.core.io as io_mod

    assert not hasattr(io_mod, "dump_yaml")
    assert "dump_yaml" not in io_mod.__all__


def test_package_logger_name_not_in_core_all():
    import autoskillit.core as core

    assert "PACKAGE_LOGGER_NAME" not in core.__all__


def test_t_typevar_not_in_core_all():
    import autoskillit.core as core

    assert "T" not in core.__all__
