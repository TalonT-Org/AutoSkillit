"""Tests for the core/ sub-package foundation layer."""

from __future__ import annotations

import importlib

import pytest


def test_core_logging_importable():
    from autoskillit.core.logging import (  # noqa: F401
        PACKAGE_LOGGER_NAME,
        configure_logging,
        get_logger,
    )


def test_core_io_importable():
    from autoskillit.core.io import (  # noqa: F401
        YAMLError,
        _atomic_write,
        dump_yaml,
        dump_yaml_str,
        ensure_project_temp,
        load_yaml,
    )


def test_old_types_module_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("autoskillit.types")


def test_old_io_module_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("autoskillit._io")


def test_old_yaml_module_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("autoskillit._yaml")


def test_old_logging_module_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("autoskillit._logging")


def test_core_io_module_has_docstring():
    import autoskillit.core.io as m

    assert m.__doc__ and len(m.__doc__.strip()) > 0


def test_dump_yaml_not_in_core_all():
    import autoskillit.core as core

    assert "dump_yaml" not in core.__all__


def test_package_logger_name_not_in_core_all():
    import autoskillit.core as core

    assert "PACKAGE_LOGGER_NAME" not in core.__all__


def test_t_typevar_not_in_core_all():
    import autoskillit.core as core

    assert "T" not in core.__all__


def test_severity_has_ok_member():
    from autoskillit.core.types import Severity

    assert Severity.OK == "ok"
    assert Severity.ERROR == "error"
    assert Severity.WARNING == "warning"
