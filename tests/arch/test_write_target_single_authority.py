"""Keep write-target extraction in the hooks scanner used by all consumers."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"


def test_write_target_consumers_share_hooks_scanner() -> None:
    import autoskillit.core as core
    import autoskillit.core.git as core_git
    import autoskillit.execution.headless._headless_recovery as recovery
    import autoskillit.hooks as hooks
    import autoskillit.server.lifecycle._guards as guards

    assert guards.scan_write_targets is hooks.scan_write_targets
    assert recovery.scan_write_targets is hooks.scan_write_targets
    assert not hasattr(core, "extract_bash_write_targets")
    assert not hasattr(core_git, "extract_bash_write_targets")


def test_no_second_write_target_implementation() -> None:
    violations: list[str] = []
    for path in (_SOURCE_ROOT).rglob("*.py"):
        if "hooks" in path.relative_to(_SOURCE_ROOT).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                re.search(r"write_targets?$", node.name)
                or re.fullmatch(r"_?resolve_write_target", node.name)
            ):
                violations.append(f"{path}:{node.lineno} {node.name}")
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            else:
                continue
            if any(name in {"_WRITE_VERBS", "WRITE_VERBS"} for name in names):
                violations.append(f"{path}:{node.lineno} write-verb registry")
    assert not violations, (
        "Use autoskillit.hooks.scan_write_targets instead of another implementation: "
        + ", ".join(violations)
    )
