"""Arch guard: a reclamation veto (_contains_reference's `references` argument) may only be
built from veto_paths() -- never from a raw, unclassified evidence extraction.

Modelled on tests/arch/test_running_status_liveness_guard.py (the #4133 liveness-verification
guard) and test_ast_rules.py's _detach_spawn_violation_reason (the #4695 fail-closed posture):
an unresolvable expression is treated as a violation, not assumed safe.

The first arch guard to cover scripts/ as well as src/autoskillit/ -- previously
scripts/pytest_tmp_lifecycle.py reimplemented IL-0 primitives in strictly weaker form with no
structural guard able to see it (scripts/ sits outside every import-linter contract, all four
custom pre-commit structural guards, and all other tests/arch/ guards, which walk only
src/autoskillit/).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SCRIPTS_ROOT = SRC_ROOT.parent.parent / "scripts"

_VETO_CONSUMER_NAMES = frozenset({"_contains_reference"})
_SAFE_VETO_SOURCE = "veto_paths"


class _VetoSourceCollector(ast.NodeVisitor):
    """Collect local variable names assigned directly from a veto_paths(...) call."""

    def __init__(self) -> None:
        self.safe_names: set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> None:
        if _is_veto_paths_call(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.safe_names.add(target.id)
        self.generic_visit(node)


def _is_veto_paths_call(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == _SAFE_VETO_SOURCE
    if isinstance(func, ast.Attribute):
        return func.attr == _SAFE_VETO_SOURCE
    return False


def _is_veto_consumer_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _VETO_CONSUMER_NAMES
    if isinstance(func, ast.Attribute):
        return func.attr in _VETO_CONSUMER_NAMES
    return False


def _references_argument(node: ast.Call) -> ast.expr | None:
    if len(node.args) >= 2:
        return node.args[1]
    for keyword in node.keywords:
        if keyword.arg == "references":
            return keyword.value
    return None


def _scan_function(fn: ast.FunctionDef | ast.AsyncFunctionDef, relpath: str) -> list[str]:
    collector = _VetoSourceCollector()
    collector.visit(fn)
    violations: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not _is_veto_consumer_call(node):
            continue
        argument = _references_argument(node)
        if argument is None:
            continue
        if _is_veto_paths_call(argument):
            continue
        if isinstance(argument, ast.Name) and argument.id in collector.safe_names:
            continue
        violations.append(
            f"{relpath}:{node.lineno} — {fn.name}() passes a references argument that is not "
            "provably the output of veto_paths(). Build any reclamation-veto input via "
            "veto_paths(); an unresolvable expression here is treated as a violation."
        )
    return violations


def _get_function_defs(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _scan_tree(root: Path, glob_pattern: str, relative_to: Path) -> list[str]:
    violations: list[str] = []
    for py_file in sorted(root.glob(glob_pattern)):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        relpath = str(py_file.relative_to(relative_to))
        for fn in _get_function_defs(tree):
            violations.extend(_scan_function(fn, relpath))
    return violations


def test_reclamation_veto_only_derives_from_veto_paths() -> None:
    """Every _contains_reference() references argument must provably come from veto_paths().

    Covers src/autoskillit/ (recursive) and scripts/ (non-recursive -- the reaper is the only
    file there today).
    """
    repo_root = SRC_ROOT.parent.parent
    violations = _scan_tree(SRC_ROOT, "**/*.py", repo_root)
    violations += _scan_tree(SCRIPTS_ROOT, "*.py", repo_root)

    assert not violations, (
        "Unclassified evidence reaching a reclamation veto position:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_unregistered_monotonic_veto_is_caught(tmp_path: Path) -> None:
    """Canary: a synthetic module that bypasses veto_paths() must be reported.

    Mirrors test_unknown_production_env_read_is_caught
    (tests/contracts/test_ambient_env_surface.py) -- proves detection happens by parsing the
    AST shape, not by looking up a hardcoded allowlist.
    """
    module_path = tmp_path / "bad_reaper.py"
    module_path.write_text(
        "def bad_reap(candidate, kernel_evidence):\n"
        "    revocable_paths = {e.path for e in kernel_evidence}\n"
        "    return _contains_reference(candidate, revocable_paths)\n"
    )
    tree = ast.parse(module_path.read_text())
    violations: list[str] = []
    for fn in _get_function_defs(tree):
        violations.extend(_scan_function(fn, "bad_reaper.py"))

    assert violations, "Guard failed to detect a references argument not sourced from veto_paths()"


def test_reverting_veto_paths_call_is_caught(tmp_path: Path) -> None:
    """Canary: reverting a veto_paths() call site to a raw set literal must be reported.

    Confirms the guard fails if S1-3's veto_paths() call in _reap is reverted to a bare
    set[Path] -- Verification 3 in the plan requires this specific regression be catchable.
    """
    module_path = tmp_path / "reverted_reaper.py"
    module_path.write_text(
        "def _reap(candidate, references):\n"
        "    return _contains_reference(candidate, references)\n"
    )
    tree = ast.parse(module_path.read_text())
    violations: list[str] = []
    for fn in _get_function_defs(tree):
        violations.extend(_scan_function(fn, "reverted_reaper.py"))

    assert violations, (
        "Guard failed to detect a bare references parameter not sourced from veto_paths()"
    )


def test_veto_paths_result_used_directly_is_not_flagged(tmp_path: Path) -> None:
    """A references argument that is a direct, inline veto_paths() call is not a violation."""
    module_path = tmp_path / "good_reaper.py"
    module_path.write_text(
        "def _reap(candidate, kernel_evidence):\n"
        "    return _contains_reference(candidate, veto_paths(kernel_evidence))\n"
    )
    tree = ast.parse(module_path.read_text())
    violations: list[str] = []
    for fn in _get_function_defs(tree):
        violations.extend(_scan_function(fn, "good_reaper.py"))

    assert not violations
