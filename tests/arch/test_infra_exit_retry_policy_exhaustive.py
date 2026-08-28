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
    policy_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_apply_infra_retry_policy"
    ]

    assert len(policy_calls) == 3
