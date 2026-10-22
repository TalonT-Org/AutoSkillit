"""Install-site registry ratchet — every install_recipe_execution call is registered.

Modelled on `tests/infra/test_canonical_json_producer_convention.py`. The registry binds
each install site to the response builder that delivers its credential. The scanner counts
real calls per module in `src/autoskillit/`, preserving multiple calls on one line. The test
fails when registered and scanned module counts diverge in either direction. A meta-test
injects a fake site to confirm the guard fires.
"""

from __future__ import annotations

import ast
from collections import Counter
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


def _scan_install_recipe_execution_sites() -> list[tuple[str, int]]:
    """AST-scan src/autoskillit/ for install_recipe_execution() call sites.

    Returns one (relative_path, line_number) tuple per call, including same-line calls.
    """
    sites: list[tuple[str, int]] = []
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
            sites.append((rel, node.lineno))
    return sites


def _install_registry_problems(registered: Counter[str], observed: Counter[str]) -> list[str]:
    """Report every module whose registered and scanned call counts differ."""
    return [
        f"{module}: registered {registered[module]}, scanned {observed[module]}"
        for module in sorted(registered.keys() | observed.keys())
        if registered[module] != observed[module]
    ]


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
    """AST ratchet: registered and scanned install calls must match by module."""

    def test_every_install_site_is_registered(self) -> None:
        scanned = _scan_install_recipe_execution_sites()
        assert scanned, "scanner returned no install_recipe_execution sites"
        registered = Counter(
            entry.installer_module for entry in RECIPE_EXECUTION_INSTALL_SITE_REGISTRY.values()
        )
        observed = Counter(path for path, _line in scanned)
        problems = _install_registry_problems(registered, observed)
        assert not problems, f"install_recipe_execution registry mismatch: {problems}"

    @pytest.mark.parametrize(
        ("registered", "observed", "expected"),
        [
            pytest.param(
                Counter({"a.py": 1, "b.py": 1}),
                Counter({"a.py": 1}),
                ["b.py: registered 1, scanned 0"],
                id="stale-registered-module",
            ),
            pytest.param(
                Counter({"a.py": 1}),
                Counter({"a.py": 2}),
                ["a.py: registered 1, scanned 2"],
                id="extra-scanned-call",
            ),
            pytest.param(
                Counter({"a.py": 2}),
                Counter({"a.py": 1}),
                ["a.py: registered 2, scanned 1"],
                id="lost-scanned-call",
            ),
            pytest.param(
                Counter({"a.py": 1}),
                Counter({"a.py": 1}),
                [],
                id="equal-counts",
            ),
        ],
    )
    def test_install_registry_diff_is_bidirectional_and_counted(
        self, registered: Counter[str], observed: Counter[str], expected: list[str]
    ) -> None:
        assert _install_registry_problems(registered, observed) == expected

    def test_install_scanner_preserves_same_line_multiplicity(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        source_root = tmp_path / "src" / "autoskillit"
        source_root.mkdir(parents=True)
        (source_root / "same_line.py").write_text(
            "install_recipe_execution(); install_recipe_execution()\n", encoding="utf-8"
        )
        monkeypatch.setattr("tests.arch.test_execution_install_delivery._SRC_ROOT", source_root)

        sites = _scan_install_recipe_execution_sites()
        assert sites == [
            ("src/autoskillit/same_line.py", 1),
            ("src/autoskillit/same_line.py", 1),
        ]
        assert Counter(path for path, _line in sites) == Counter(
            {"src/autoskillit/same_line.py": 2}
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

        def patched_scan() -> list[tuple[str, int]]:
            sites = original_scan()
            sites.append(("src/autoskillit/fake_install_module.py", 1))
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
