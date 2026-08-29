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

    # Walk every FunctionDef body and find any call to _apply_infra_retry_policy,
    # recording the enclosing function name so the call site must match an
    # expected stale/idle/main path.
    policy_calls: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body_src = ast.dump(node)
        if "_apply_infra_retry_policy" not in body_src:
            continue
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "_apply_infra_retry_policy"
            ):
                policy_calls.append((node.name, call.lineno))

    assert len(policy_calls) == 3, (
        f"expected exactly 3 policy call sites, found {len(policy_calls)}: {policy_calls}"
    )
    # Verify the main-path policy call lives inside _build_skill_result, not
    # anywhere in the module — the stale and idle branches live in two named
    # local-variable assignments; the main path is identified by being the
    # only one that receives the runtime-computed infra_category.
    enclosing = {name for name, _ in policy_calls}
    assert "_build_skill_result" in enclosing, (
        f"main-path policy call expected inside _build_skill_result, found in: {enclosing}"
    )
