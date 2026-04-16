"""Tests for the core/ sub-package foundation layer."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_atomic_write_docstring_contains_atomic_keyword():
    import autoskillit.core.io as m

    assert m.__doc__ and "atomic" in m.__doc__


def test_dump_yaml_not_in_core_all():
    import autoskillit.core as core
    import autoskillit.core.io as core_io

    assert "dump_yaml" not in core.__all__
    assert "dump_yaml" not in core_io.__all__
    assert not hasattr(core_io, "dump_yaml")


def test_package_logger_name_not_in_core_all():
    import autoskillit.core as core

    assert "PACKAGE_LOGGER_NAME" not in core.__all__


def test_t_typevar_not_in_core_all():
    import autoskillit.core as core

    assert "T" not in core.__all__
