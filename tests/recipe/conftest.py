"""Shared test helpers for tests/recipe/."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoskillit.core.types import Severity
from autoskillit.recipe.io import _parse_step, builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import RuleFinding
from autoskillit.recipe.schema import Recipe, RecipeStep

BUNDLED_RECIPE_NAMES: list[str] = [
    "implementation",
    "remediation",
    "implementation-groups",
    "merge-prs",
    "full-audit",
]


def assert_no_rule_errors(
    findings: list[RuleFinding],
    *,
    context: str = "",
) -> None:
    """Assert that findings contain no ERROR-severity violations."""
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert not errors, (
        f"Unexpected ERROR findings{f' in {context}' if context else ''}: "
        f"{[(f.rule, f.step_name, f.message) for f in errors]}"
    )


@pytest.fixture(scope="module")
def pmp_recipe():
    return load_recipe(builtin_recipes_dir() / "merge-prs.yaml")


@pytest.fixture(scope="module")
def impl_recipe():
    return load_recipe(builtin_recipes_dir() / "implementation.yaml")


@pytest.fixture(scope="module")
def remed_recipe():
    return load_recipe(builtin_recipes_dir() / "remediation.yaml")


@pytest.fixture(scope="module")
def impl_groups_recipe():
    return load_recipe(builtin_recipes_dir() / "implementation-groups.yaml")


def _make_workflow(steps: dict[str, dict]) -> Recipe:
    parsed_steps = {name: _parse_step(data) for name, data in steps.items()}
    return Recipe(name="test", description="test", steps=parsed_steps, kitchen_rules=["test"])


def _build_merge_worktree_recipe(capture: dict) -> Recipe:
    """Helper: build a minimal Recipe with a merge_worktree step and the given capture dict."""
    return Recipe(
        name="test-merge",
        description="Test merge recipe",
        summary="merge > done",
        steps={
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "${{ context.worktree_path }}", "base_branch": "main"},
                capture=capture,
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done"),
        },
    )


# ---------------------------------------------------------------------------
# Shared fixture data: valid recipe dict and YAML writer
# ---------------------------------------------------------------------------

VALID_RECIPE = {
    "name": "test-recipe",
    "description": "A test recipe",
    "ingredients": {
        "test_dir": {"description": "Dir to test", "required": True},
        "branch": {"description": "Branch", "default": "main"},
    },
    "kitchen_rules": ["NEVER use native tools"],
    "steps": {
        "run_tests": {
            "tool": "test_check",
            "with": {"worktree_path": "${{ inputs.test_dir }}"},
            "on_success": "done",
            "on_failure": "escalate",
        },
        "done": {"action": "stop", "message": "Tests passed."},
        "escalate": {"action": "stop", "message": "Need help."},
    },
}


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.dump(data, default_flow_style=False))
    return path


PRIMARY_CI_EVENT_KEYS = {"ci_event", "conflict_ci_event"}


def build_reverse_graph(steps: dict) -> dict[str, set[str]]:
    """Build a reverse routing graph: step -> set of steps that route to it."""
    reverse: dict[str, set[str]] = {}
    for name, step in steps.items():
        for key in ("on_success", "on_failure"):
            target = step.get(key)
            if target:
                reverse.setdefault(target, set()).add(name)
        on_result = step.get("on_result", [])
        if isinstance(on_result, list):
            for cond in on_result:
                if isinstance(cond, dict):
                    target = cond.get("route")
                    if target:
                        reverse.setdefault(target, set()).add(name)
                elif isinstance(cond, str):
                    reverse.setdefault(cond, set()).add(name)
        elif isinstance(on_result, dict):
            for target in on_result.get("routes", {}).values():
                if target:
                    reverse.setdefault(target, set()).add(name)
    return reverse


def has_wait_for_ci_predecessor(steps: dict, step_name: str, reverse_graph: dict) -> bool:
    """Return True if a wait_for_ci step exists upstream of step_name."""
    visited: set[str] = set()
    queue = list(reverse_graph.get(step_name, set()))
    while queue:
        node = queue.pop()
        if node in visited:
            continue
        visited.add(node)
        node_step = steps.get(node, {})
        if node_step.get("tool") == "wait_for_ci":
            return True
        queue.extend(reverse_graph.get(node, set()) - visited)
    return False


def reaches_wait_for_ci(steps: dict, start: str, depth: int = 5) -> bool:
    """BFS from start to check if wait_for_ci is reachable within depth hops."""
    visited: set[str] = set()
    queue = [start]
    for _ in range(depth):
        next_queue: list[str] = []
        for node in queue:
            if node in visited:
                continue
            visited.add(node)
            node_step = steps.get(node, {})
            if node_step.get("tool") == "wait_for_ci":
                return True
            for key in ("on_success", "on_failure"):
                target = node_step.get(key)
                if target and target in steps and target not in visited:
                    next_queue.append(target)
            on_result = node_step.get("on_result", [])
            if isinstance(on_result, list):
                for cond in on_result:
                    if isinstance(cond, dict):
                        target = cond.get("route")
                    elif isinstance(cond, str):
                        target = cond
                    else:
                        continue
                    if target and target in steps and target not in visited:
                        next_queue.append(target)
            elif isinstance(on_result, dict):
                for target in on_result.get("routes", {}).values():
                    if target and target in steps and target not in visited:
                        next_queue.append(target)
        queue = next_queue
        if not queue:
            break
    return False


# ---------------------------------------------------------------------------
# Audit trail test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_report_frontmatter() -> dict:
    return {
        "experiment_type": "causal_inference",
        "methodology_traditions": ["controlled_intervention"],
        "disambiguation_rule_applied": None,
        "tier_c_lens": "vis-lens-methodology-norms",
        "design_review_verdict": "GO",
        "classification_timestamp": "2026-04-13T15:32:00Z",
        "audit_trail_path": {
            "design_review": "research/test-slug/audit/design-review-dashboard.md",
            "visualization_trace": "research/test-slug/audit/visualization-plan-trace.md",
        },
    }


@pytest.fixture
def sample_report_text(sample_report_frontmatter: dict) -> str:
    fm = yaml.dump(sample_report_frontmatter, default_flow_style=False, sort_keys=False)
    return f"---\n{fm}---\n\n# Test Report\n\nBody content."
