"""Shared test helpers for tests/recipe/."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoskillit.recipe.io import _parse_step, builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import Recipe, RecipeStep

NO_AUTOSKILLIT_IMPORT = "no-autoskillit-import-in-skill-python-block"
_CONTRACT_POSITIONAL = frozenset(
    {
        "contract-unsatisfied-input",
        "contract-unreferenced-required",
    }
)

KNOWN_VIOLATIONS_BY_RECIPE: dict[str, frozenset[str]] = {
    "research-design": frozenset({NO_AUTOSKILLIT_IMPORT}) | _CONTRACT_POSITIONAL,
    "research": frozenset({"unbounded-cycle", NO_AUTOSKILLIT_IMPORT}) | _CONTRACT_POSITIONAL,
    "research-review": frozenset({NO_AUTOSKILLIT_IMPORT}) | _CONTRACT_POSITIONAL,
    "implementation": frozenset({"unbounded-cycle", NO_AUTOSKILLIT_IMPORT}) | _CONTRACT_POSITIONAL,
    "implementation-groups": frozenset({"unbounded-cycle", NO_AUTOSKILLIT_IMPORT})
    | _CONTRACT_POSITIONAL,
    "merge-prs": frozenset({"unbounded-cycle", NO_AUTOSKILLIT_IMPORT}) | _CONTRACT_POSITIONAL,
    "planner": frozenset({"unbounded-cycle", NO_AUTOSKILLIT_IMPORT}) | _CONTRACT_POSITIONAL,
    "promote-to-main-wrapper": frozenset({NO_AUTOSKILLIT_IMPORT}),
    "remediation": frozenset({"unbounded-cycle", NO_AUTOSKILLIT_IMPORT}) | _CONTRACT_POSITIONAL,
    "research-implement": frozenset({"unbounded-cycle", NO_AUTOSKILLIT_IMPORT})
    | _CONTRACT_POSITIONAL,
}

KNOWN_PART_B_VIOLATIONS = frozenset().union(*KNOWN_VIOLATIONS_BY_RECIPE.values())


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
