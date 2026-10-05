"""Prevent run-skill attestation markers from becoming watchdog keys."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.medium


def _call_name(node: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _is_run_skill_liveness_call(node: ast.Call) -> bool:
    parts = _call_name(node.func).split(".")
    return parts[-1:] == ["run_headless_core"] or (
        len(parts) >= 2
        and parts[-1] == "run"
        and parts[-2] in {"executor", "HeadlessExecutor", "DefaultHeadlessExecutor"}
    )


def test_run_skill_marker_is_not_passed_as_a_watchdog_key() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for layer in ("execution", "server"):
        source_root = repository_root / "src" / "autoskillit" / layer
        for source_path in source_root.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not _is_run_skill_liveness_call(node):
                    continue
                if any(keyword.arg == "marker_dir" for keyword in node.keywords):
                    relative_path = source_path.relative_to(repository_root)
                    violations.append(f"{relative_path}:{node.lineno}")

    assert not violations, (
        "the run-skill marker is the fabricated-completion attestation; "
        "supervisor liveness comes from operation leases\n" + "\n".join(violations)
    )
