"""Tests for the core/ sub-package foundation layer."""

from __future__ import annotations


def test_core_logging_importable():
    from autoskillit.core.logging import (  # noqa: F401
        PACKAGE_LOGGER_NAME,
        configure_logging,
        get_logger,
    )

    assert callable(get_logger)
    assert callable(configure_logging)


def test_core_io_importable():
    from autoskillit.core.io import (  # noqa: F401
        YAMLError,
        _atomic_write,
        dump_yaml,
        dump_yaml_str,
        ensure_project_temp,
        load_yaml,
    )

    assert callable(load_yaml)
    assert callable(dump_yaml)


def test_core_io_module_has_docstring():
    import autoskillit.core.io as m

    assert m.__doc__ and "atomic" in m.__doc__


def test_dump_yaml_not_in_core_all():
    import autoskillit.core as core

    assert "dump_yaml" not in core.__all__


def test_package_logger_name_not_in_core_all():
    import autoskillit.core as core

    assert "PACKAGE_LOGGER_NAME" not in core.__all__


def test_t_typevar_not_in_core_all():
    import autoskillit.core as core

    assert "T" not in core.__all__
