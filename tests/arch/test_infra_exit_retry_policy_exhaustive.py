"""The infrastructure retry policy has one exhaustive authority."""

from __future__ import annotations

import ast

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.medium]


def _policy_function(tree: ast.Module) -> ast.FunctionDef | None:
    return next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_apply_infra_retry_policy"
        ),
        None,
    )


def _function_calls(function: ast.FunctionDef, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def test_infra_retry_policy_is_exhaustive() -> None:
    tree = ast.parse((SRC_ROOT / "execution/headless/_headless_result.py").read_text())
    policy = _policy_function(tree)

    assert policy is not None, "_apply_infra_retry_policy must be the sole retry-policy authority"
    assert any(isinstance(node, ast.Match) for node in ast.walk(policy))
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "assert_never"
        for node in ast.walk(policy)
    )


def test_stale_idle_and_main_paths_delegate_to_the_shared_retry_policy() -> None:
    tree = ast.parse((SRC_ROOT / "execution/headless/_headless_result.py").read_text())
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    main_path = functions["_build_skill_result"]
    stall_helper = functions["_build_stall_result"]

    assert len(_function_calls(stall_helper, "_apply_infra_retry_policy")) == 1

    stall_specs = {
        keyword.value.id
        for call in _function_calls(main_path, "_build_stall_result")
        for keyword in call.keywords
        if keyword.arg == "stall_spec" and isinstance(keyword.value, ast.Name)
    }
    assert stall_specs == {"_STALE_SPEC", "_IDLE_STALL_SPEC"}

    main_policy_calls = _function_calls(main_path, "_apply_infra_retry_policy")
    assert len(main_policy_calls) == 1
    assert isinstance(main_policy_calls[0].args[0], ast.Name)
    assert main_policy_calls[0].args[0].id == "infra_category"
