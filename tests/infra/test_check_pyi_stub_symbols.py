"""Tests for scripts/check_pyi_stub_symbols.py pre-commit hook."""

from __future__ import annotations

import importlib.util

import pytest

from autoskillit.core.paths import pkg_root

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _load_check_module():
    spec = importlib.util.spec_from_file_location(
        "check_pyi_stub_symbols",
        pkg_root().parent.parent / "scripts" / "check_pyi_stub_symbols.py",
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_check_pyi_symbols_detects_missing_symbol(tmp_path):
    """The hook must report a public symbol present in a submodule but absent from the stub."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()

    submod = pkg / "foo.py"
    submod.write_text("class Bar:\n    pass\n\nclass Baz:\n    pass\n")

    pyi = pkg / "__init__.pyi"
    pyi.write_text("from .foo import Bar as Bar\n")

    mod = _load_check_module()
    violations = mod.check_file(pyi)
    assert len(violations) == 1
    assert "Baz" in violations[0]


def test_check_pyi_symbols_accepts_complete_stub(tmp_path):
    """The hook must accept a stub that covers all public symbols."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()

    submod = pkg / "foo.py"
    submod.write_text("class Bar:\n    pass\n\ndef baz():\n    pass\n")

    pyi = pkg / "__init__.pyi"
    pyi.write_text("from .foo import Bar as Bar\nfrom .foo import baz as baz\n")

    mod = _load_check_module()
    violations = mod.check_file(pyi)
    assert violations == []


def test_check_pyi_symbols_skips_underscore_names(tmp_path):
    """Private names (starting with _) should not be flagged as missing."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()

    submod = pkg / "foo.py"
    submod.write_text("class Bar:\n    pass\n\ndef _helper():\n    pass\n")

    pyi = pkg / "__init__.pyi"
    pyi.write_text("from .foo import Bar as Bar\n")

    mod = _load_check_module()
    violations = mod.check_file(pyi)
    assert violations == []


def test_check_pyi_symbols_uses_all_when_defined(tmp_path):
    """When __all__ is defined, only __all__ members count as public."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()

    submod = pkg / "foo.py"
    submod.write_text('__all__ = ["Bar"]\n\nclass Bar:\n    pass\n\nclass Baz:\n    pass\n')

    pyi = pkg / "__init__.pyi"
    pyi.write_text("from .foo import Bar as Bar\n")

    mod = _load_check_module()
    violations = mod.check_file(pyi)
    assert violations == []
