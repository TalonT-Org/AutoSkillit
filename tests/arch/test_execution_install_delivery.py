"""Install-site registry ratchet — every install_recipe_execution site is registered.

Modelled on `tests/infra/test_canonical_json_producer_convention.py`. The registry binds
each install site to the response builder that delivers its credential. The scanner finds
real call sites in `src/autoskillit/`; the test fails when the two diverge in either
direction (added or removed site). A meta-test injects a fake site to confirm the
guard fires.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.core import RECIPE_EXECUTION_INSTALL_SITE_REGISTRY, all_tool_names

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]


_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"


def _is_install_recipe_execution_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "install_recipe_execution":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "install_recipe_execution":
        return True
    return False


def _scan_install_recipe_execution_sites() -> set[tuple[str, int]]:
    """AST-scan src/autoskillit/ for install_recipe_execution() call sites.

    Returns set of (relative_path, line_number) tuples.
    """
    sites: set[tuple[str, int]] = set()
    for py_file in _SRC_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_install_recipe_execution_call(node):
                continue
            rel = str(py_file.relative_to(_SRC_ROOT.parent.parent))
            sites.add((rel, node.lineno))
    return sites


def _function_calls_function(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef, target_name: str
) -> bool:
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == target_name:
            return True
        if isinstance(func, ast.Attribute) and func.attr == target_name:
            return True
    return False


class TestExecutionInstallDelivery:
    """AST ratchet: every install_recipe_execution site must be registered."""

    def test_every_install_site_is_registered(self) -> None:
        registered_modules = {
            entry.installer_module for entry in RECIPE_EXECUTION_INSTALL_SITE_REGISTRY.values()
        }
        scanned = _scan_install_recipe_execution_sites()
        assert scanned, "scanner returned no install_recipe_execution sites"
        scanner_modules = {site[0] for site in scanned}
        missing = scanner_modules - registered_modules
        assert not missing, (
            f"install_recipe_execution sites without registered entries: {sorted(missing)}"
        )

    def test_registered_delivering_symbols_call_the_sanctioned_producer(self) -> None:
        for entry in RECIPE_EXECUTION_INSTALL_SITE_REGISTRY.values():
            module_path = _SRC_ROOT.parent.parent / entry.delivering_module
            tree = ast.parse(module_path.read_text(), filename=str(module_path))
            # Locate the delivering_symbol specifically
            target_func: ast.FunctionDef | ast.AsyncFunctionDef | None = None
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == entry.delivering_symbol
                ):
                    target_func = node
                    break
            assert target_func is not None, (
                f"delivering_symbol {entry.delivering_symbol!r} not found in "
                f"{entry.delivering_module}"
            )
            assert _function_calls_function(target_func, "build_recipe_execution_credential"), (
                f"{entry.delivering_symbol!r} in {entry.delivering_module} must call "
                f"build_recipe_execution_credential"
            )

    def test_unregistered_install_site_fails_the_ratchet(self, monkeypatch) -> None:
        """Meta-test: a fake install site should cause the ratchet to fail."""
        original_scan = _scan_install_recipe_execution_sites

        def patched_scan() -> set[tuple[str, int]]:
            sites = original_scan()
            sites.add(("src/autoskillit/fake_install_module.py", 1))
            return sites

        monkeypatch.setattr(
            "tests.arch.test_execution_install_delivery._scan_install_recipe_execution_sites",
            patched_scan,
        )
        with pytest.raises(AssertionError, match="fake_install_module"):
            self.test_every_install_site_is_registered()

    def test_registered_delivery_surfaces_are_real_tools(self) -> None:
        registered_tools = all_tool_names()
        assert registered_tools, "tool registry returned no names"
        assert RECIPE_EXECUTION_INSTALL_SITE_REGISTRY, "install-site registry is empty"
        unknown = sorted(
            entry.delivery_surface
            for entry in RECIPE_EXECUTION_INSTALL_SITE_REGISTRY.values()
            if entry.delivery_surface not in registered_tools
        )
        assert not unknown, (
            f"delivery_surface values that are not registered MCP tool names: {unknown}"
        )
