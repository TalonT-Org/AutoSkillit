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


def test_core_init_uses_lazy_getattr():
    import autoskillit.core as core

    assert hasattr(core, "__getattr__")
    assert callable(core.__getattr__)


def test_core_init_has_no_eager_submodule_imports():
    import ast

    from autoskillit.core.paths import pkg_root

    init_path = pkg_root() / "core" / "__init__.py"
    tree = ast.parse(init_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            pytest.fail(f"Eager relative import found: from .{node.module} import ...")


def test_core_pyi_stub_exists():
    from autoskillit.core.paths import pkg_root

    pyi = pkg_root() / "core" / "__init__.pyi"
    assert pyi.exists(), f"Missing stub: {pyi}"


def test_core_pyi_stub_consistent_with_all():
    import ast

    import autoskillit.core as core
    from autoskillit.core.paths import pkg_root

    pyi = pkg_root() / "core" / "__init__.pyi"
    tree = ast.parse(pyi.read_text())
    stub_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                stub_names.add(alias.asname or alias.name)

    all_set = set(core.__all__)
    private_reexports = core._PRIVATE_REEXPORTS
    assert all_set <= stub_names, f"Names in __all__ missing from stub: {all_set - stub_names}"
    assert stub_names - all_set == private_reexports, (
        f"Unexpected stub-only names: {(stub_names - all_set) - private_reexports}"
    )


def test_core_pyi_uses_as_form():
    import ast

    from autoskillit.core.paths import pkg_root

    pyi = pkg_root() / "core" / "__init__.pyi"
    tree = ast.parse(pyi.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.asname == alias.name, (
                    f"Missing 'as' form: {alias.name} from .{node.module}"
                )


def test_closure_walk_does_not_detect_lazy_init():
    import sys
    from pathlib import Path

    from autoskillit.core.paths import pkg_root

    tests_dir = Path(pkg_root()).parent.parent / "tests"
    sys.path.insert(0, str(tests_dir))
    try:
        from _test_filter import _expand_reexport_closure

        src_root = pkg_root()
        changed = {"core/paths.py"}
        expanded = _expand_reexport_closure(changed, src_root)
        assert "core/__init__.py" not in expanded, (
            "Closure walk detected lazy __init__.py — PEP 562 migration incomplete"
        )
    finally:
        sys.path.remove(str(tests_dir))
