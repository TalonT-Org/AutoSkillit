"""Cross-check: every recipe-dispatched run_skill step must have
corresponding skill contract tests in tests/skills/ or tests/contracts/.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.recipe.io import list_recipes, load_recipe

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_TESTS_DIR = Path(__file__).resolve().parent.parent

_SKILL_CMD_RE = re.compile(r"^/(?:autoskillit:)?([a-z0-9-]+)")


def _skill_based_steps() -> list[tuple[str, str]]:
    """Return (recipe_name, skill_name) for all run_skill steps in bundled recipes."""
    pairs = []
    result = list_recipes(project_dir=Path("/nonexistent"))
    for info in result.items:
        recipe = load_recipe(info.path)
        for step in recipe.steps.values():
            if step.tool == "run_skill":
                cmd = step.with_args.get("skill_command", "")
                m = _SKILL_CMD_RE.match(cmd)
                if m:
                    pairs.append((info.name, m.group(1)))
    return pairs


def _has_contract_tests(skill_name: str) -> bool:
    """Check whether a skill has contract tests in tests/skills/ or tests/contracts/."""
    normalized = skill_name.replace("-", "_")
    for search_dir in (_TESTS_DIR / "skills", _TESTS_DIR / "contracts"):
        if not search_dir.is_dir():
            continue
        for f in search_dir.iterdir():
            if f.name.startswith("test_") and normalized in f.name and f.name.endswith(".py"):
                return True
    return False


def test_recipe_skills_have_contract_tests() -> None:
    """Every skill dispatched by a recipe run_skill step should have contract tests."""
    KNOWN_EXCEPTIONS = {
        "audit-tests",
        "audit-cohesion",
        "audit-arch",
        "audit-feature-gates",
        "audit-docs",
        "audit-review-decisions",
        "make-plan",
        "review-approach",
        "audit-impl",
        "rectify",
        "compose-pr",
        "prepare-pr",
        "scope",
        "bem-scope",
        "diagnose-ci",
        "investigate",
        "implement-worktree-no-merge",
        "resolve-failures",
        "retry-worktree",
        "resolve-merge-conflicts",
        "build-execution-map",
        "promote-to-main",
        "planner-analyze",
        "planner-generate-phases",
        "planner-elaborate-phase",
        "planner-refine-phases",
        "planner-elaborate-assignments",
        "planner-refine-assignments",
        "planner-elaborate-wps",
        "planner-refine-wps",
        "planner-consolidate-wps",
        "planner-validate-task-alignment",
        "planner-reconcile-deps",
        "planner-assess-review-approach",
        "select-directions",
        "setup-environment",
        "make-groups",
        "exp-lens-",
        "compose-research-pr",
        "audit-claims",
        "bundle-local-report",
        "classify-experiment-type",
        "apply-review-dimensions",
    }
    failures: list[str] = []
    for recipe_name, skill_name in _skill_based_steps():
        if skill_name in KNOWN_EXCEPTIONS:
            continue
        if not _has_contract_tests(skill_name):
            failures.append(f"  {recipe_name} dispatches {skill_name} — no contract tests found")
    assert not failures, (
        "Recipe-dispatched skills must have contract tests in "
        "tests/skills/ or tests/contracts/:\n" + "\n".join(failures)
    )
