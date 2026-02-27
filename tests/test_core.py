"""Tests for the core/ sub-package foundation layer."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest


def test_core_package_importable():
    import autoskillit.core  # noqa: F401


def test_core_types_importable():
    from autoskillit.core.types import (  # noqa: F401
        CONTEXT_EXHAUSTION_MARKER,
        PIPELINE_FORBIDDEN_TOOLS,
        RETRY_RESPONSE_FIELDS,
        SKILL_TOOLS,
        LoadReport,
        LoadResult,
        MergeFailedStep,
        MergeState,
        RecipeSource,
        RestartScope,
        RetryReason,
        Severity,
        SubprocessResult,
        SubprocessRunner,
    )


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

    assert callable(_atomic_write)
    assert callable(load_yaml)


def test_core_init_reexports_types():
    from autoskillit.core import MergeFailedStep, SubprocessResult  # noqa: F401


def test_core_init_reexports_logging():
    from autoskillit.core import configure_logging, get_logger  # noqa: F401


def test_core_init_reexports_io():
    from autoskillit.core import YAMLError, _atomic_write, dump_yaml, load_yaml  # noqa: F401


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


def test_core_modules_have_no_intra_package_imports():
    """Verify core/ sub-modules import nothing from autoskillit.*."""
    core_dir = Path(__file__).parent.parent / "src" / "autoskillit" / "core"
    for py_file in core_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("autoskillit."), (
                        f"{py_file.name} imports from autoskillit.*: {node.module}"
                    )
