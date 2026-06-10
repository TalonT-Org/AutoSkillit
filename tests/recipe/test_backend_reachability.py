"""Backend-parametrized recipe reachability tests.

Complementary to TestBundledRecipeBackendCompat (which asserts findings fire on
the unpruned recipe): these tests assert that the pruned recipe — the artifact
actually served to the orchestrator — has zero backend-incompatible-skill
findings for every registered backend.
"""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.execution.backends import BACKEND_REGISTRY, get_backend
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe._recipe_composition import _prune_skipped_steps
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.server.tools._auto_overrides import _backend_capability_overrides
from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RECIPE_NAMES = ["implementation", "implementation-groups", "remediation"]
_BACKEND_NAMES = sorted(BACKEND_REGISTRY.keys())

# Steps guarded by inputs.open_pr rather than backend_supports_git_write.
# Under codex, no PR is ever created (no git write), so these are
# structurally unreachable at the orchestration level even though they
# survive ingredient-based pruning.
_ROUTE_GUARDED_COMPAT_EXCEPTIONS: frozenset[str] = frozenset(
    {
        "resolve_queue_merge_conflicts",
        "resolve_direct_merge_conflicts",
        "resolve_immediate_merge_conflicts",
        "ci_conflict_fix",
    }
)


class TestPrunedRecipeSatisfiability:
    """REQ-SAT-001/002: pruned recipe x backend -> zero ERROR findings."""

    @pytest.fixture(scope="class")
    def resolver(self) -> DefaultSkillResolver:
        return DefaultSkillResolver()

    @pytest.mark.parametrize("recipe_name", _RECIPE_NAMES, ids=lambda x: x)
    @pytest.mark.parametrize("backend_name", _BACKEND_NAMES, ids=lambda x: x)
    def test_pruned_recipe_has_no_backend_incompatible_findings(
        self, recipe_name: str, backend_name: str, resolver: DefaultSkillResolver
    ) -> None:
        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        backend = get_backend(backend_name)
        overrides = _backend_capability_overrides(backend)

        pruned_recipe, _resolutions = _prune_skipped_steps(recipe, overrides)

        ctx = make_validation_context(
            pruned_recipe,
            backend_name=backend_name,
            skill_resolver=resolver,
        )
        findings = run_semantic_rules(ctx)
        compat_errors = [
            f
            for f in findings
            if f.rule == "backend-incompatible-skill"
            and f.severity == Severity.ERROR
            and f.step_name not in _ROUTE_GUARDED_COMPAT_EXCEPTIONS
        ]
        assert not compat_errors, (
            f"Pruned {recipe_name} has backend-incompatible steps under "
            f"{backend_name}: {[f.step_name for f in compat_errors]}"
        )

    @pytest.mark.parametrize("recipe_name", _RECIPE_NAMES, ids=lambda x: x)
    def test_codex_pruning_removes_git_write_steps(self, recipe_name: str) -> None:
        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        guarded_steps = {
            name
            for name, step in recipe.steps.items()
            if step.skip_when_false == "inputs.backend_supports_git_write"
        }
        assert guarded_steps, f"No guarded steps found in {recipe_name}"

        pruned_recipe, _resolutions = _prune_skipped_steps(
            recipe, {"backend_supports_git_write": "false"}
        )
        surviving = guarded_steps & set(pruned_recipe.steps)
        assert not surviving, f"Guarded steps survived codex pruning in {recipe_name}: {surviving}"
