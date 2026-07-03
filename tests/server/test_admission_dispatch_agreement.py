"""Admission ↔ dispatch agreement test — the structural contract.

For every bundled recipe × backend combination, when admission says
``dispatch_feasible=True`` (and the recipe validates), every surviving
``run_skill`` step must pass the dispatch-time ``_is_backend_incompatible``
gate using the same per-step effective backend that ``run_skill`` computes.

This is the keystone test that makes the whole class of admission-vs-dispatch
disagreement bugs impossible to regress. Without it, admission could silently
admit pipelines that crash at ``run_skill`` time — the bug class fixed in
the rectify admission-truth-telling effort.

Requires ``DefaultSkillResolver`` to resolve real SKILL.md files from the
installed package. Filesystem access is required (no network).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.execution.backends import BACKEND_REGISTRY, get_backend
from autoskillit.recipe._api import load_and_validate
from autoskillit.recipe.io import builtin_recipes_dir
from autoskillit.recipe.io import load_recipe as load_recipe_yaml
from autoskillit.recipe.schema import Recipe, RecipeStep
from autoskillit.server.tools._auto_overrides import (
    _backend_capability_overrides,
    _compute_effective_backend_map,
)
from autoskillit.server.tools.tools_execution import _is_backend_incompatible
from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_BUILTIN_DIR = builtin_recipes_dir()
_RECIPES: tuple[str, ...] = (
    "implementation",
    "implementation-groups",
    "remediation",
)
_BACKENDS: tuple[str, ...] = tuple(sorted(BACKEND_REGISTRY.keys()))
_SKILL_RESOLVER = DefaultSkillResolver()


def _step_effective_backend(
    step: RecipeStep,
    step_name: str,
    backend_name: str,
    effective_map: dict[str, str] | None,
) -> str | None:
    """Mirror the per-step effective backend logic from tools_execution.py."""
    step_provider = getattr(step, "provider", "") or ""
    # Replicate the dispatch decision: ANTHROPIC_BASE_URL → claude-code override.
    # We don't have ProvidersConfig here; use the effective_backend_map if
    # provided (computed at IL-3 call sites with provider config).
    if effective_map and step_name in effective_map:
        return effective_map[step_name]
    # Fallback: if step has explicit provider at the recipe level, treat that
    # as a routing hint (the real code uses _resolve_provider_profile).
    if step_provider:
        # Without ProvidersConfig we cannot fully resolve provider_extras; for
        # this test we accept step_provider as a hint of claude-code routing
        # (the production schema only uses provider for that purpose).
        return "claude-code"
    return backend_name


def _dispatch_effective_backends(
    recipe: Recipe,
    backend_name: str,
    effective_map: dict[str, str] | None,
) -> dict[str, str]:
    """Compute dispatch-time effective backend for every run_skill step."""
    result: dict[str, str] = {}
    for step_name, step in recipe.steps.items():
        if getattr(step, "tool", None) != "run_skill":
            continue
        backend_result = _step_effective_backend(step, step_name, backend_name, effective_map)
        if backend_result is not None:
            result[step_name] = backend_result
    return result


@pytest.mark.parametrize("recipe_name", _RECIPES, ids=lambda x: x)
@pytest.mark.parametrize("backend_name", _BACKENDS, ids=lambda x: x)
def test_admission_dispatch_agreement(
    recipe_name: str, backend_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admission agreement: when load_and_validate returns dispatch_feasible=True,
    every surviving run_skill step must pass _is_backend_incompatible against
    the dispatch-time effective backend."""
    monkeypatch.setattr(
        "autoskillit.server.tools._auto_overrides.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )
    backend = get_backend(backend_name)
    ingredient_overrides = _backend_capability_overrides(backend)

    # Pre-load recipe to compute effective backend map (mirrors IL-3 call sites).
    raw_recipe = load_recipe_yaml(_BUILTIN_DIR / f"{recipe_name}.yaml")
    effective_map = _compute_effective_backend_map(
        raw_recipe.steps,
        backend_name,
        None,
        recipe_name,
        skill_resolver=_SKILL_RESOLVER,
    )

    result = load_and_validate(
        recipe_name,
        project_dir=_PROJECT_ROOT,
        backend_name=backend_name,
        ingredient_overrides=ingredient_overrides,
        effective_backend_map=effective_map,
        lister=_SKILL_RESOLVER,
    )

    if not result.get("dispatch_feasible", True):
        # If admission refuses feasibility, the contract is trivially satisfied.
        return

    if not result.get("valid", False):
        # If admission produced errors, contract still holds (not feasible).
        return

    # Admission says feasible + valid → dispatch must agree for every step.
    dispatch_backends = _dispatch_effective_backends(raw_recipe, backend_name, effective_map)
    unresolvable: list[str] = []
    violations: list[str] = []

    for step_name, step in raw_recipe.steps.items():
        if getattr(step, "tool", None) != "run_skill":
            continue
        skill_cmd = getattr(step, "with_args", {}).get("skill_command", "")
        if "${" in skill_cmd or "<" in skill_cmd or "{" in skill_cmd:
            continue
        skill_name = skill_cmd.lstrip("/").split()[0] if skill_cmd.lstrip("/") else ""
        skill_name = skill_name.removeprefix("autoskillit:")
        if not skill_name:
            continue
        skill_info = _SKILL_RESOLVER.resolve(skill_name)
        if skill_info is None:
            unresolvable.append(
                f"{recipe_name}/{step_name}: skill '{skill_name}' is not resolvable "
                f"via DefaultSkillResolver — cannot verify dispatch compatibility"
            )
            continue
        step_effective = dispatch_backends.get(step_name)
        if step_effective is None:
            continue
        if _is_backend_incompatible(skill_info, step_effective):
            violations.append(
                f"{recipe_name}/{step_name}: admission says dispatch_feasible=True "
                f"but skill '{skill_name}' requires {sorted(skill_info.backend_requirements)} "
                f"and effective backend is '{step_effective}'"
            )

    assert not violations, "Admission ↔ dispatch agreement violated:\n  " + "\n  ".join(violations)
    assert not unresolvable, "Unresolvable skills (test infra gap):\n  " + "\n  ".join(
        unresolvable
    )
