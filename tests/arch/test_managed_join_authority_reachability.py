"""Keep managed-join attestation authority behavior wired to production."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

from tests.contracts._ast_helpers import call_name

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "autoskillit"
_PROTOCOL_PATH = _SRC_ROOT / "core" / "types" / "_type_protocols_infra.py"
_EXPECTED_CALLERS = Counter(
    {
        ("issue", "server/managed_join_prelaunch.py", "prepare_managed_join_context"): 1,
        (
            "find_verified_context",
            "server/tools/tools_execution/_fixed_batch_handlers.py",
            "_request_facts",
        ): 1,
        ("verify", "server/_managed_join_attestation.py", "find_verified_context"): 1,
        ("verify", "server/_managed_join_attestation.py", "_recover_verified_context"): 1,
    }
)


def _protocol_methods() -> set[str]:
    tree = ast.parse(_PROTOCOL_PATH.read_text(encoding="utf-8"), filename=str(_PROTOCOL_PATH))
    protocol = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ManagedJoinAttestationAuthority"
    )
    return {
        node.name
        for node in protocol.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _production_method_callers() -> Counter[tuple[str, str, str]]:
    inventory: Counter[tuple[str, str, str]] = Counter()
    method_names = _protocol_methods() - {"activation_epoch"}
    expected_paths = {path for _method, path, _function in _EXPECTED_CALLERS}
    for relative_path in expected_paths:
        path = _SRC_ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            method_name = call_name(node)
            if method_name not in method_names:
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                parent = parents.get(parent)
            if parent is not None:
                inventory[(method_name, relative_path, parent.name)] += 1
    return inventory


def _factory_wires_default_authority() -> bool:
    factory_path = _SRC_ROOT / "server" / "_factory.py"
    tree = ast.parse(factory_path.read_text(encoding="utf-8"), filename=str(factory_path))
    authority_names = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and call_name(node.value) == "DefaultManagedJoinAttestationAuthority"
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    return any(
        call_name(node) == "ToolContext"
        and any(
            keyword.arg == "managed_join_attestation_authority"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id in authority_names
            for keyword in node.keywords
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    )


def test_managed_join_authority_protocol_methods_have_production_callers() -> None:
    assert _protocol_methods() >= {"issue", "verify", "find_verified_context"}
    assert _production_method_callers() == _EXPECTED_CALLERS


def test_tool_context_receives_the_default_managed_join_authority() -> None:
    assert _factory_wires_default_authority()
