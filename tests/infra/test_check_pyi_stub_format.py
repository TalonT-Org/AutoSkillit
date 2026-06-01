"""Tests for scripts/check_pyi_stub_format.py pre-commit hook."""

from __future__ import annotations

import importlib.util

import pytest

from autoskillit.core.paths import pkg_root

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _load_check_module():
    spec = importlib.util.spec_from_file_location(
        "check_pyi_stub_format",
        pkg_root().parent.parent / "scripts" / "check_pyi_stub_format.py",
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_check_pyi_rejects_function_def(tmp_path):
    """The pre-commit hook must reject def statements in __init__.pyi."""
    pyi = tmp_path / "__init__.pyi"
    pyi.write_text("from .foo import bar as bar\ndef baz(): ...\n")

    mod = _load_check_module()
    violations = mod.check_file(pyi)
    assert len(violations) == 1
    assert "FunctionDef" in violations[0]


def test_check_pyi_accepts_valid_file(tmp_path):
    """The pre-commit hook must accept a valid re-export-only pyi."""
    pyi = tmp_path / "__init__.pyi"
    pyi.write_text("from .foo import bar as bar\nfrom .baz import qux as qux\n")

    mod = _load_check_module()
    violations = mod.check_file(pyi)
    assert violations == []


def test_check_pyi_rejects_class_def(tmp_path):
    """The pre-commit hook must reject class statements in __init__.pyi."""
    pyi = tmp_path / "__init__.pyi"
    pyi.write_text("from .foo import bar as bar\nclass Baz: ...\n")

    mod = _load_check_module()
    violations = mod.check_file(pyi)
    assert len(violations) == 1
    assert "ClassDef" in violations[0]


def test_check_pyi_rejects_non_relative_import(tmp_path):
    """The pre-commit hook must reject absolute imports in __init__.pyi."""
    pyi = tmp_path / "__init__.pyi"
    pyi.write_text("from typing import Any\n")

    mod = _load_check_module()
    violations = mod.check_file(pyi)
    assert len(violations) >= 1
    assert any("level 0" in v for v in violations)
