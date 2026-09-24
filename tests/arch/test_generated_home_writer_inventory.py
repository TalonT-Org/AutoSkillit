"""Keep generated-home writes and verification at their intended boundaries."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

from tests.contracts._ast_helpers import call_name

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
_BACKEND_PROTOCOL = _SRC_ROOT / "core" / "types" / "_type_protocols_backend.py"
_EXPECTED_CALLERS = Counter(
    {
        (
            "ensure_pre_launch",
            "workspace/session_skills/_materialization.py",
            "_setup_generated_session",
        ): 1,
        (
            "ensure_pre_launch",
            "workspace/session_skills/_materialization.py",
            "_restore_session",
        ): 1,
        (
            "ensure_pre_launch",
            "cli/session/_session_launch.py",
            "prepare_interactive_launch",
        ): 1,
        ("ensure_pre_launch", "execution/backends/claude.py", "probe_launch_readiness"): 1,
        (
            "probe_launch_readiness",
            "cli/session/_session_launch.py",
            "prepare_interactive_launch",
        ): 1,
        (
            "configure_managed_session_dir",
            "workspace/session_skills/_materialization.py",
            "_configure_managed_session_route",
        ): 1,
        ("codex_prelaunch_transaction", "execution/backends/codex.py", "ensure_pre_launch"): 1,
        (
            "verify_managed_session_dir",
            "cli/session/_session_backend.py",
            "verify_launch_home",
        ): 1,
        (
            "verify_managed_session_dir",
            "server/_managed_join_attestation.py",
            "_verified_live_catalog",
        ): 1,
        (
            "read_managed_session_catalog",
            "server/_managed_join_attestation.py",
            "_verified_live_catalog",
        ): 1,
        (
            "projected_manifest_path",
            "server/_managed_join_attestation.py",
            "_write_managed_parent_binding",
        ): 1,
    }
)


def _managed_route_methods() -> set[str]:
    tree = ast.parse(
        _BACKEND_PROTOCOL.read_text(encoding="utf-8"), filename=str(_BACKEND_PROTOCOL)
    )
    protocol = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ManagedRouteHomeBackend"
    )
    return {
        node.name
        for node in protocol.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _production_calls() -> tuple[Counter[tuple[str, str, str]], list[tuple[str, int, str]]]:
    inventory: Counter[tuple[str, str, str]] = Counter()
    dynamic_calls: list[tuple[str, int, str]] = []
    route_methods = _managed_route_methods()
    names = route_methods | {
        "ensure_pre_launch",
        "probe_launch_readiness",
        "project_managed_route",
        "codex_prelaunch_transaction",
    }
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        relpath = path.relative_to(_SRC_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relpath)
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = call_name(node)
            if name == "getattr" and len(node.args) >= 2:
                method_arg = node.args[1]
                if (
                    isinstance(method_arg, ast.Constant)
                    and isinstance(method_arg.value, str)
                    and method_arg.value in route_methods
                ):
                    dynamic_calls.append((relpath, node.lineno, method_arg.value))
            if name not in names:
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                parent = parents.get(parent)
            if parent is not None:
                inventory[(name, relpath, parent.name)] += 1
    return inventory, dynamic_calls


def test_generated_home_writer_and_verifier_callers_are_inventoried() -> None:
    calls, dynamic_calls = _production_calls()
    assert not dynamic_calls, f"managed-route getattr calls bypass inventory: {dynamic_calls}"
    assert calls == _EXPECTED_CALLERS


def test_codex_exposes_managed_projection_through_protocol_method() -> None:
    codex_path = _SRC_ROOT / "execution" / "backends" / "codex.py"
    tree = ast.parse(codex_path.read_text(encoding="utf-8"), filename=str(codex_path))
    backend = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "CodexBackend"
    )
    assert any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "configure_managed_session_dir"
            for target in node.targets
        )
        and isinstance(node.value, ast.Name)
        and node.value.id == "project_managed_route"
        for node in backend.body
    )
