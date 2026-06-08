"""Tests for the unrouted-callable-verdict and callable-verdict-requires-on-result
semantic rules (recipe-routing-deadlock immunity, #3889).
"""

from __future__ import annotations

from typing import Any

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.contracts import load_bundled_manifest
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.rules.dataflow import rules_dataflow_callable as _r
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="test-callable-verdict-routing",
        description="Test recipe for callable verdict routing rules.",
        version="0.1.0",
        kitchen_rules=["test"],
        steps=steps,
    )


def _callable_manifest(contract: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal manifest containing exactly one callable_contracts entry."""
    return {"callable_contracts": {"autoskillit.test.fake_callable": contract}}


def test_unrouted_callable_verdict_fires_when_value_missing_no_catch_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule fires ERROR when an allowed_value is unrouted and there is no catch-all arm."""
    manifest = _callable_manifest(
        {
            "inputs": [{"name": "worktree_path", "type": "str", "required": True}],
            "outputs": [
                {
                    "name": "committed",
                    "type": "str",
                    "allowed_values": ["a", "b", "c"],
                }
            ],
        }
    )
    monkeypatch.setattr(_r, "load_bundled_manifest", lambda: manifest)

    step = RecipeStep(
        tool="run_python",
        with_args={
            "callable": "autoskillit.test.fake_callable",
            "worktree_path": "/tmp",
        },
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(
                    when="${{ result.committed }} == 'a'",
                    route="next_step",
                ),
                StepResultCondition(
                    when="${{ result.committed }} == 'b'",
                    route="next_step",
                ),
                # No catch-all and no explicit condition for 'c' — should fire.
            ]
        ),
    )
    recipe = _make_recipe(
        {
            "guard": step,
            "next_step": RecipeStep(action="stop", message="ok"),
        }
    )
    findings = run_semantic_rules(recipe)
    unrouted = [f for f in findings if f.rule == "unrouted-callable-verdict"]
    assert len(unrouted) == 1
    assert unrouted[0].severity == Severity.ERROR
    assert unrouted[0].step_name == "guard"
    assert "'c'" in unrouted[0].message


def test_unrouted_callable_verdict_tolerates_catch_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule does NOT fire when unrouted values are covered by a catch-all arm."""
    manifest = _callable_manifest(
        {
            "inputs": [{"name": "worktree_path", "type": "str", "required": True}],
            "outputs": [
                {
                    "name": "committed",
                    "type": "str",
                    "allowed_values": ["a", "b", "c"],
                }
            ],
        }
    )
    monkeypatch.setattr(_r, "load_bundled_manifest", lambda: manifest)

    step = RecipeStep(
        tool="run_python",
        with_args={
            "callable": "autoskillit.test.fake_callable",
            "worktree_path": "/tmp",
        },
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(
                    when="${{ result.committed }} == 'a'",
                    route="error_step",
                ),
                # 'b' and 'c' fall through to catch-all — acceptable.
                StepResultCondition(route="next_step"),
            ]
        ),
    )
    recipe = _make_recipe(
        {
            "guard": step,
            "next_step": RecipeStep(action="stop", message="ok"),
            "error_step": RecipeStep(action="stop", message="error"),
        }
    )
    findings = run_semantic_rules(recipe)
    unrouted = [f for f in findings if f.rule == "unrouted-callable-verdict"]
    assert unrouted == []


def test_unrouted_callable_verdict_passes_when_all_values_routed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule produces no findings when every allowed_value has an explicit on_result condition."""
    manifest = _callable_manifest(
        {
            "inputs": [{"name": "worktree_path", "type": "str", "required": True}],
            "outputs": [
                {
                    "name": "committed",
                    "type": "str",
                    "allowed_values": ["a", "b", "c"],
                }
            ],
        }
    )
    monkeypatch.setattr(_r, "load_bundled_manifest", lambda: manifest)

    step = RecipeStep(
        tool="run_python",
        with_args={
            "callable": "autoskillit.test.fake_callable",
            "worktree_path": "/tmp",
        },
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(
                    when="${{ result.committed }} == 'a'",
                    route="next_step",
                ),
                StepResultCondition(
                    when="${{ result.committed }} == 'b'",
                    route="next_step",
                ),
                StepResultCondition(
                    when="${{ result.committed }} == 'c'",
                    route="next_step",
                ),
            ]
        ),
    )
    recipe = _make_recipe(
        {
            "guard": step,
            "next_step": RecipeStep(action="stop", message="ok"),
        }
    )
    findings = run_semantic_rules(recipe)
    unrouted = [f for f in findings if f.rule == "unrouted-callable-verdict"]
    assert unrouted == []


def test_callable_verdict_requires_on_result_fires_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule fires ERROR when a callable with allowed_values uses scalar on_success/on_failure."""
    manifest = _callable_manifest(
        {
            "inputs": [{"name": "worktree_path", "type": "str", "required": True}],
            "outputs": [
                {
                    "name": "committed",
                    "type": "str",
                    "allowed_values": ["false", "true", "regression_detected"],
                }
            ],
        }
    )
    monkeypatch.setattr(_r, "load_bundled_manifest", lambda: manifest)

    step = RecipeStep(
        tool="run_python",
        with_args={
            "callable": "autoskillit.test.fake_callable",
            "worktree_path": "/tmp",
        },
        on_success="next_step",
        on_failure="next_step",
    )
    recipe = _make_recipe(
        {
            "guard": step,
            "next_step": RecipeStep(action="stop", message="ok"),
        }
    )
    findings = run_semantic_rules(recipe)
    requires = [f for f in findings if f.rule == "callable-verdict-requires-on-result"]
    assert len(requires) == 1
    assert requires[0].severity == Severity.ERROR
    assert requires[0].step_name == "guard"
    assert "regression_detected" in requires[0].message


def test_callable_verdict_requires_on_result_passes_when_no_allowed_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule produces no findings when the callable contract has no allowed_values outputs."""
    manifest = _callable_manifest(
        {
            "inputs": [{"name": "worktree_path", "type": "str", "required": True}],
            "outputs": [{"name": "status", "type": "str"}],
        }
    )
    monkeypatch.setattr(_r, "load_bundled_manifest", lambda: manifest)

    step = RecipeStep(
        tool="run_python",
        with_args={
            "callable": "autoskillit.test.fake_callable",
            "worktree_path": "/tmp",
        },
        on_success="next_step",
        on_failure="next_step",
    )
    recipe = _make_recipe(
        {
            "guard": step,
            "next_step": RecipeStep(action="stop", message="ok"),
        }
    )
    findings = run_semantic_rules(recipe)
    requires = [f for f in findings if f.rule == "callable-verdict-requires-on-result"]
    assert requires == []


def test_real_callable_contract_uses_real_manifest() -> None:
    """Sanity check: the real bundled manifest surfaces allowed_values for the new contracts.

    This guards the contract manifest itself: a regression that strips the new
    contracts (or breaks the loader) would surface here.
    """
    manifest = load_bundled_manifest()
    callables = manifest.get("callable_contracts", {})

    commit_guard = callables.get("autoskillit.recipe._cmd_rpc.commit_guard")
    assert commit_guard is not None
    committed_out = next((o for o in commit_guard["outputs"] if o["name"] == "committed"), None)
    assert committed_out is not None
    assert "allowed_values" in committed_out, (
        "commit_guard contract must declare allowed_values for the regression_detected immunity"
    )

    main_repo_guard = callables.get("autoskillit.recipe._cmd_rpc.main_repo_guard")
    assert main_repo_guard is not None, "main_repo_guard must be in callable_contracts"
    cleaned_out = next((o for o in main_repo_guard["outputs"] if o["name"] == "cleaned"), None)
    assert cleaned_out is not None
    assert "allowed_values" in cleaned_out
