"""Tests for reachability-aware vacuous gate detection in _is_vacuous_gate.

The vacuous gate exemption (`_is_vacuous_gate` returning True) allowed
dispatch to pass admission control when all guarded steps were pruned —
but route-repair in `_prune_skipped_steps` can redirect upstream steps
directly to the gate, making it reachable and executable in the
post-prune flow graph. These tests pin the correct behavior:

- A gate reachable from the entry step is NOT vacuous (it will execute
  and fail at runtime → must be reported as infeasible).
- A gate with no incoming routing edges is vacuous (all guards were
  pruned and nothing routes to it → safe to exempt).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class _StubStep:
    tool: str | None = None
    action: str | None = None
    note: str | None = None
    with_args: dict | None = None
    on_success: str | None = None
    on_failure: str | None = None
    on_context_limit: str | None = None
    on_rate_limit: str | None = None
    on_exhausted: str | None = None
    on_result: object | None = None
    skip_when_false: str | None = None
    optional: bool = False
    sub_recipe: str | None = None


@dataclass
class _StubRecipe:
    steps: dict = field(default_factory=dict)
    ingredients: dict = field(default_factory=dict)


class _StubPreStep:
    def __init__(self, skip_when_false: str) -> None:
        self.skip_when_false = skip_when_false


def _build_recipe(steps: dict[str, dict]) -> _StubRecipe:
    """Build a Recipe-like object from a flat step-dict (name -> fields)."""
    stub_steps: dict[str, _StubStep] = {}
    for step_name, fields in steps.items():
        stub_steps[step_name] = _StubStep(**fields)
    return _StubRecipe(steps=stub_steps)


def test_is_vacuous_gate_returns_false_when_gate_reachable_post_prune() -> None:
    """Gate is reachable via create_impl_worktree.on_success → gate_backend_write.

    With `implement` pruned but `create_impl_worktree.on_success` redirected to
    `gate_backend_write` by route-repair, the gate is reachable from the entry
    step. _is_vacuous_gate must return False (not vacuous) → infeasible.
    """
    from autoskillit.recipe._recipe_composition import _is_vacuous_gate

    recipe = _build_recipe(
        {
            "create_impl_worktree": {"on_success": "gate_backend_write"},
            "gate_backend_write": {
                "tool": "run_python",
                "with_args": {
                    "callable": "autoskillit.smoke_utils.gate_backend_write",
                    "backend_supports_git_write": "false",
                },
                "on_failure": "escalate",
                "on_exhausted": "escalate",
            },
        }
    )

    gate_input_keys = {"backend_supports_git_write"}
    pre_prune_steps = {
        "implement": _StubPreStep(skip_when_false="inputs.backend_supports_git_write"),
    }
    skip_resolutions: dict = {"implement": False}
    post_prune_steps = recipe.steps

    result = _is_vacuous_gate(
        gate_input_keys,
        gate_step_name="gate_backend_write",
        skip_resolutions=skip_resolutions,
        pre_prune_steps=pre_prune_steps,
        post_prune_steps=post_prune_steps,
        post_prune_recipe=recipe,
    )

    assert result is False, (
        "Gate is reachable via create_impl_worktree.on_success after route-repair; "
        "must NOT be treated as vacuous."
    )


def test_is_vacuous_gate_returns_true_when_gate_unreachable_post_prune() -> None:
    """Gate has no incoming routing edges (truly unreachable after pruning).

    When all guarded steps are pruned AND no other step routes to the gate,
    the gate is truly unreachable. _is_vacuous_gate must return True (vacuous).
    """
    from autoskillit.recipe._recipe_composition import _is_vacuous_gate

    recipe = _build_recipe(
        {
            "create_impl_worktree": {"on_success": "done"},
            "gate_backend_write": {
                "tool": "run_python",
                "with_args": {
                    "callable": "autoskillit.smoke_utils.gate_backend_write",
                    "backend_supports_git_write": "false",
                },
                "on_failure": "escalate",
                "on_exhausted": "escalate",
            },
        }
    )

    gate_input_keys = {"backend_supports_git_write"}
    pre_prune_steps = {
        "implement": _StubPreStep(skip_when_false="inputs.backend_supports_git_write"),
    }
    skip_resolutions: dict = {"implement": False}
    post_prune_steps = recipe.steps

    result = _is_vacuous_gate(
        gate_input_keys,
        gate_step_name="gate_backend_write",
        skip_resolutions=skip_resolutions,
        pre_prune_steps=pre_prune_steps,
        post_prune_steps=post_prune_steps,
        post_prune_recipe=recipe,
    )

    assert result is True, (
        "Gate has no incoming routing edges and all guards pruned; must be treated as vacuous."
    )


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation", "remediation", "implementation-groups"],
)
def test_compute_capability_feasibility_returns_infeasible_for_codex_recipes(
    recipe_name: str,
) -> None:
    """All three gate-equipped recipes must report dispatch_feasible=False
    when backend_supports_git_write=false under codex backend.

    Route-repair redirects create_impl_worktree.on_success to gate_backend_write
    after pruning `implement`, making the gate reachable. Admission control
    must identify this as an infeasible pipeline.

    After the worker_routable discriminator fix, merge-conflict steps use
    git_metadata_write (worker_routable=True, required_backends=frozenset()),
    so the backend-incompatible-skill rule does NOT fire for them. The recipe
    is valid=True. Infeasibility comes exclusively from gate_backend_write.
    """
    from autoskillit.recipe._api import load_and_validate

    result = load_and_validate(
        recipe_name,
        project_dir=_PROJECT_ROOT,
        ingredient_overrides={"backend_supports_git_write": "false"},
        backend_name="codex",
    )
    assert result["valid"] is True, (
        f"Recipe '{recipe_name}' with codex backend must be valid=True: "
        f"git_metadata_write skills are worker_routable (required_backends=frozenset()) "
        f"so no backend-incompatible-skill findings are expected"
    )
    assert result.get("dispatch_feasible") is False, (
        f"Recipe '{recipe_name}' with backend_supports_git_write=false under codex "
        f"must report dispatch_feasible=False (gate is reachable post-prune); "
        f"got: {result.get('dispatch_feasible')}"
    )
    assert "gate_backend_write" in result.get("infeasible_steps", []), (
        f"Recipe '{recipe_name}' must list gate_backend_write in infeasible_steps; "
        f"got: {result.get('infeasible_steps')}"
    )


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation", "remediation", "implementation-groups"],
)
def test_compute_capability_feasibility_feasible_when_capability_route_active(
    recipe_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R0: when capability-driven routing fires (skill_resolver + binary present),
    the effective backend map routes git_metadata_write steps to claude-code,
    making dispatch_feasible=True for codex+implementation. Decision record: gate
    is vacuous-by-design on codex when the binary is present."""
    from autoskillit.recipe._api import load_and_validate
    from autoskillit.recipe.io import find_recipe_by_name, load_recipe
    from autoskillit.server.tools._auto_overrides import _compute_effective_backend_map
    from autoskillit.workspace.skills import DefaultSkillResolver

    monkeypatch.setattr(
        "autoskillit.server.tools._auto_overrides.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )
    resolver = DefaultSkillResolver()
    _info = find_recipe_by_name(recipe_name, _PROJECT_ROOT)
    assert _info is not None, f"Recipe {recipe_name!r} not found"
    raw = load_recipe(_info.path)
    eff_map, _ = _compute_effective_backend_map(
        raw.steps,
        "codex",
        None,
        recipe_name,
        skill_resolver=resolver,
    )
    assert eff_map is not None
    result = load_and_validate(
        recipe_name,
        project_dir=_PROJECT_ROOT,
        effective_backend_map=eff_map,
        ingredient_overrides={"backend_supports_git_write": "true"},
        backend_name="codex",
        lister=resolver,
    )
    assert result.get("dispatch_feasible") is True, (
        f"Recipe '{recipe_name}' with capability route active must be dispatch_feasible=True"
    )


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation", "remediation", "implementation-groups"],
)
def test_capability_route_binary_absent_fails_closed(
    recipe_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R0 REQ-TEST-007 (binary-absent half): real recipe YAML, real resolver,
    real backend registry, only shutil.which mocked. With the claude binary
    absent the capability route fails closed — the override stays 'false'
    (resolution_path='capability_route_no_binary'), guarded steps prune, and
    admission refuses with the gate reachable instead of admitting a pipeline
    that would crash at dispatch."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from autoskillit.recipe._api import load_and_validate
    from autoskillit.recipe.io import find_recipe_by_name, load_recipe
    from autoskillit.server.tools._auto_overrides import (
        _compute_effective_backend_map,
        _provider_aware_capability_overrides,
    )
    from autoskillit.workspace.skills import DefaultSkillResolver

    monkeypatch.setattr(
        "autoskillit.server.tools._auto_overrides.shutil.which",
        lambda name: None,
    )
    resolver = DefaultSkillResolver()
    _info = find_recipe_by_name(recipe_name, _PROJECT_ROOT)
    assert _info is not None, f"Recipe {recipe_name!r} not found"
    raw = load_recipe(_info.path)

    # Minimal codex-shaped backend stand-in: tests/recipe may not import
    # autoskillit.execution (layer boundary), and only these two capability
    # flags are consulted by the override computation.
    codex_like = MagicMock()
    codex_like.name = "codex"
    codex_like.capabilities = SimpleNamespace(
        git_metadata_writable=False,
        anthropic_provider_capable=False,
    )
    overrides, detail = _provider_aware_capability_overrides(
        codex_like,
        recipe_name,
        None,
        raw.steps,
        skill_resolver=resolver,
    )
    assert overrides["backend_supports_git_write"] == "false"
    assert detail.resolution_path == "capability_route_no_binary"

    eff_map, _ = _compute_effective_backend_map(
        raw.steps,
        "codex",
        None,
        recipe_name,
        skill_resolver=resolver,
    )
    result = load_and_validate(
        recipe_name,
        project_dir=_PROJECT_ROOT,
        effective_backend_map=eff_map,
        ingredient_overrides=overrides,
        backend_name="codex",
        lister=resolver,
    )
    assert result.get("dispatch_feasible") is False, (
        f"Recipe '{recipe_name}' with binary absent must refuse (fail closed); "
        f"got dispatch_feasible={result.get('dispatch_feasible')}"
    )
    assert "gate_backend_write" in result.get("infeasible_steps", []), (
        f"Refusal must name the reachable gate; got: {result.get('infeasible_steps')}"
    )
