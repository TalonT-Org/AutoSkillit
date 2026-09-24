"""The write-target scan is the shared authority for hook guards."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_HOOKS_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "hooks"
_OLD_EXTRACTORS = {"extract_redirect_targets_with_status", "extract_write_verb_targets"}
_REMOVED_API = "extract_redirect_targets"


@pytest.mark.parametrize(
    "guard_name",
    ["installation_integrity_guard", "write_guard", "git_ops_guard"],
)
def test_guards_call_shared_write_target_scan(guard_name: str) -> None:
    path = _HOOKS_ROOT / "guards" / f"{guard_name}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
    }

    assert "scan_write_targets" in calls, f"{path} must call the shared scan"
    assert not calls & _OLD_EXTRACTORS, f"{path} still calls a separate target extractor"


def _defines_or_exports_removed_api(node: ast.AST) -> bool:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name == _REMOVED_API
    if isinstance(node, ast.Name):
        return node.id == _REMOVED_API and isinstance(node.ctx, ast.Store)
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return any(
            (alias.asname or alias.name.rsplit(".", 1)[-1]) == _REMOVED_API for alias in node.names
        )
    if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
        return False
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(
        isinstance(target, ast.Name) and target.id in {"__all__", "_NAME_TO_MODULE"}
        for target in targets
    ) and any(
        isinstance(value, ast.Constant) and value.value == _REMOVED_API
        for value in ast.walk(node.value)
    )


def test_status_less_redirect_api_is_not_defined_or_exported() -> None:
    violations: list[str] = []
    for path in sorted(_HOOKS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations.extend(
            f"{path}:{getattr(node, 'lineno', '?')}"
            for node in ast.walk(tree)
            if _defines_or_exports_removed_api(node)
        )

    assert not violations, "Status-less redirect API remains available: " + ", ".join(violations)
