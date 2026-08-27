"""Per-family focused tests for the routing sibling (R1, R2, R3).

Each test exercises ONE rule in isolation. The routing module owns
``merge-routing-incomplete``, ``merge-routing-cross-site-consistency``,
and ``merge-failure-skill-domain-mismatch``.

Recipe structure notes:
- ``on_result`` is a LIST of condition dicts (not a dict).
- Tool arguments go under the ``with`` key.
- ``on_result`` conditions get parsed into ``StepResultCondition``s.
"""

from __future__ import annotations

import pytest

from tests.recipe.rules_merge._helpers import (
    assert_rule_does_not_fire,
    assert_rule_fires,
    build_recipe,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _merge_step(
    *,
    name: str,
    conditions: list[dict],
    on_success: str = "done",
    on_failure: str = "done",
) -> dict:
    return {
        name: {
            "tool": "merge_worktree",
            "with": {"worktree_path": "x", "base_branch": "main"},
            "on_result": conditions,
            "on_success": on_success,
            "on_failure": on_failure,
        },
    }


def test_r1_routes_only_one_recoverable_failure_fires_incomplete() -> None:
    """R1 fires when a merge_worktree step routes only a subset of recoverable failures."""
    recipe = build_recipe(
        {
            **_merge_step(
                name="merge",
                conditions=[
                    {
                        "route": "fix_gate",
                        "when": "result.failed_step == 'test_gate'",
                    },
                ],
            ),
            "fix_gate": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:resolve-failures"},
            },
            "done": {"action": "stop"},
        },
    )
    assert_rule_fires(recipe, rule_name="merge-routing-incomplete")


def test_r2_two_sites_with_different_recovery_classes_fires_cross_site() -> None:
    """R2 fires when two sites disagree on the recovery class for ``rebase``."""
    recipe = build_recipe(
        {
            **_merge_step(
                name="merge_a",
                conditions=[
                    {
                        "route": "fix_a",
                        "when": "result.failed_step == 'rebase'",
                    },
                ],
            ),
            "fix_a": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:resolve-merge-conflicts"},
            },
            **_merge_step(
                name="merge_b",
                conditions=[
                    {
                        "route": "fix_b",
                        "when": "result.failed_step == 'rebase'",
                    },
                ],
            ),
            "fix_b": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:make-plan"},
            },
            "done": {"action": "stop"},
        },
    )
    assert_rule_fires(recipe, rule_name="merge-routing-cross-site-consistency")


def test_r3_rebase_routed_to_resolve_failures_fires_domain_mismatch() -> None:
    """R3 fires when rebase routes to ``resolve-failures`` (wrong skill)."""
    recipe = build_recipe(
        {
            **_merge_step(
                name="merge",
                conditions=[
                    {
                        "route": "fix_rebase",
                        "when": "result.failed_step == 'rebase'",
                    },
                ],
            ),
            "fix_rebase": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:resolve-failures"},
            },
            "done": {"action": "stop"},
        },
    )
    assert_rule_fires(recipe, rule_name="merge-failure-skill-domain-mismatch")


def test_r1_clean_routing_does_not_fire() -> None:
    """R1 does NOT fire when all seven recoverable failures are routed."""
    all_failures = (
        "dirty_tree",
        "test_gate",
        "test_gate_contention",
        "post_rebase_test_gate",
        "rebase",
        "dirty_main_repo",
        "ref_coherence",
    )
    conditions = [
        {"route": f"fix_{name}", "when": f"result.failed_step == '{name}'"}
        for name in all_failures
    ]
    terminal_steps = {f"fix_{name}": {"action": "stop"} for name in all_failures}
    recipe = build_recipe(
        {
            **_merge_step(name="merge", conditions=conditions),
            **terminal_steps,
            "done": {"action": "stop"},
        },
    )
    assert_rule_does_not_fire(recipe, rule_name="merge-routing-incomplete")
