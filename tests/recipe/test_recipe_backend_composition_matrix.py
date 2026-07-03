"""Recipe x backend composition matrix -- full cross-product CI gate.

Validates every bundled recipe composes validly under every registered backend.
DECLARED_UNSUPPORTED governs by-design unsupported combos; orphan and
collection-count meta-tests prevent skip-rot and matrix shrinkage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import Severity
from autoskillit.execution.backends import BACKEND_REGISTRY, get_backend
from autoskillit.recipe._api import load_and_validate
from autoskillit.recipe.io import all_validated_recipe_names, builtin_recipes_dir, load_recipe
from autoskillit.server.tools._auto_overrides import (
    _backend_capability_overrides,
    _compute_effective_backend_map,
)
from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_ALL_RECIPE_NAMES = sorted(all_validated_recipe_names(_PROJECT_ROOT))
_BACKEND_NAMES = sorted(BACKEND_REGISTRY.keys())


# -- By-design unsupported combos (skip) ------------------------------------
DECLARED_UNSUPPORTED: frozenset[tuple[str, str]] = frozenset()

UNSUPPORTED_REASONS: dict[tuple[str, str], dict[str, str]] = {}

_MATRIX_IDS: list[tuple[str, str]] = [
    (r, b) for r in _ALL_RECIPE_NAMES for b in _BACKEND_NAMES if (r, b) not in DECLARED_UNSUPPORTED
]


# -- Known-broken combos (xfail strict) -------------------------------------
KNOWN_BROKEN: dict[tuple[str, str], str] = {
    ("agent-eval", "claude-code"): (
        "tracking: #4069 -- violates all-dispatchable-stops-have-sentinel + dead-output"
    ),
    ("agent-eval", "codex"): (
        "tracking: #4069 -- violates all-dispatchable-stops-have-sentinel + dead-output"
    ),
    ("skill-eval", "claude-code"): (
        "tracking: #4069 -- violates all-dispatchable-stops-have-sentinel + dead-output"
    ),
    ("skill-eval", "codex"): (
        "tracking: #4069 -- violates all-dispatchable-stops-have-sentinel + dead-output"
    ),
}

_SKILL_RESOLVER = DefaultSkillResolver()


def _apply_marks(matrix_ids: list[tuple[str, str]]) -> list[Any]:
    """Wrap matrix tuples in pytest.param, attaching xfail marks for KNOWN_BROKEN."""
    params: list[Any] = []
    for r, b in matrix_ids:
        marks: list[Any] = []
        if (r, b) in KNOWN_BROKEN:
            marks.append(pytest.mark.xfail(strict=True, reason=KNOWN_BROKEN[(r, b)]))
        params.append(pytest.param(r, b, marks=marks, id=f"{r}/{b}"))
    return params


@pytest.mark.parametrize("recipe_name,backend_name", _apply_marks(_MATRIX_IDS))
def test_recipe_backend_matrix_cell(recipe_name: str, backend_name: str, monkeypatch) -> None:
    monkeypatch.setattr(
        "autoskillit.server.tools._auto_overrides.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )
    backend = get_backend(backend_name)
    _raw = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    _eff_map = _compute_effective_backend_map(
        _raw.steps,
        backend_name,
        None,
        recipe_name,
        skill_resolver=_SKILL_RESOLVER,
    )
    result = load_and_validate(
        recipe_name,
        project_dir=_PROJECT_ROOT,
        backend_name=backend_name,
        effective_backend_map=_eff_map,
        ingredient_overrides=_backend_capability_overrides(backend),
        lister=_SKILL_RESOLVER,
    )

    assert result["valid"] is True, (
        f"Recipe '{recipe_name}' invalid on backend '{backend_name}': "
        + "; ".join(
            f"[{s.get('rule')}] {s.get('message', '')[:80]}"
            for s in result.get("suggestions", [])
            if s.get("severity") == Severity.ERROR
        )
    )
    assert len(result.get("content", "")) > 0, (
        f"Recipe '{recipe_name}' on backend '{backend_name}' produced empty content"
    )

    if backend_name == "codex" and "gate_backend_write" in (result.get("infeasible_steps") or []):
        assert result.get("dispatch_feasible") is False, (
            f"Recipe '{recipe_name}' has reachable gate under codex but "
            f"dispatch_feasible is not False — admission control regression."
        )

    suggestions: list[dict[str, Any]] = result.get("suggestions", [])
    dangling = [
        s for s in suggestions if s.get("message", "").startswith("[post-prune] dangling route:")
    ]
    assert not dangling, (
        f"Recipe '{recipe_name}' on backend '{backend_name}' has dangling routes: "
        + "; ".join(s.get("message", "") for s in dangling)
    )

    backend_compat_errors = [
        s
        for s in suggestions
        if s.get("rule") == "backend-incompatible-skill" and s.get("severity") == Severity.ERROR
    ]
    assert not backend_compat_errors, (
        f"Recipe '{recipe_name}' on backend '{backend_name}' has "
        f"backend-incompatible-skill errors: "
        + "; ".join(s.get("message", "") for s in backend_compat_errors)
    )


@pytest.mark.parametrize(
    "recipe_name,backend_name",
    [pytest.param(r, b, id=f"{r}/{b}") for r, b in _MATRIX_IDS],
)
def test_dispatch_feasible_per_backend(recipe_name: str, backend_name: str, monkeypatch) -> None:
    """Dispatch feasibility must reflect gate_backend_write reachability per backend."""
    monkeypatch.setattr(
        "autoskillit.server.tools._auto_overrides.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )
    backend = get_backend(backend_name)
    _raw = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    _eff_map = _compute_effective_backend_map(
        _raw.steps,
        backend_name,
        None,
        recipe_name,
        skill_resolver=_SKILL_RESOLVER,
    )
    result = load_and_validate(
        recipe_name,
        project_dir=_PROJECT_ROOT,
        backend_name=backend_name,
        effective_backend_map=_eff_map,
        ingredient_overrides=_backend_capability_overrides(backend),
        lister=_SKILL_RESOLVER,
    )

    infeasible = result.get("infeasible_steps") or []
    if backend_name == "codex" and "gate_backend_write" in infeasible:
        assert result.get("dispatch_feasible") is False, (
            f"Recipe '{recipe_name}' has reachable gate_backend_write under codex but "
            f"dispatch_feasible is not False"
        )
    else:
        assert result.get("dispatch_feasible") is True, (
            f"Recipe '{recipe_name}' on backend '{backend_name}' expected "
            f"dispatch_feasible=True (no infeasible gate), "
            f"got {result.get('dispatch_feasible')}"
        )


def test_declared_unsupported_orphan_check() -> None:
    """Every DECLARED_UNSUPPORTED entry must reference a live recipe and backend."""
    recipe_names = frozenset(_ALL_RECIPE_NAMES)
    backend_names = frozenset(BACKEND_REGISTRY.keys())
    for recipe_name, backend_name in DECLARED_UNSUPPORTED:
        assert recipe_name in recipe_names, (
            f"DECLARED_UNSUPPORTED entry ({recipe_name!r}, {backend_name!r}) "
            f"references unknown recipe {recipe_name!r}. "
            f"Valid: {sorted(recipe_names)}"
        )
        assert backend_name in backend_names, (
            f"DECLARED_UNSUPPORTED entry ({recipe_name!r}, {backend_name!r}) "
            f"references unknown backend {backend_name!r}. "
            f"Valid: {sorted(backend_names)}"
        )


def test_matrix_collection_count() -> None:
    """Matrix size = recipes x backends - declared unsupported."""
    expected = len(_ALL_RECIPE_NAMES) * len(_BACKEND_NAMES) - len(DECLARED_UNSUPPORTED)
    assert len(_MATRIX_IDS) == expected, (
        f"Matrix size mismatch: got {len(_MATRIX_IDS)}, "
        f"expected {len(_ALL_RECIPE_NAMES)} recipes x {len(_BACKEND_NAMES)} backends "
        f"- {len(DECLARED_UNSUPPORTED)} unsupported = {expected}"
    )


def test_recipe_backend_matrix_codex_with_provider_override() -> None:
    """Codex with backend_supports_git_write=true (simulated via ingredient_overrides)
    but no effective_backend_map: admission truth-telling flags merge-conflict steps
    whose skill requires claude-code, because the rule evaluates per-step effective
    backends and falls back to ctx.backend_name='codex' without a map."""
    result = load_and_validate(
        "implementation",
        project_dir=_PROJECT_ROOT,
        backend_name="codex",
        ingredient_overrides={"backend_supports_git_write": "true"},
        lister=_SKILL_RESOLVER,
    )
    assert result["valid"] is False, (
        "Expected valid=False: merge-conflict steps invoke resolve-merge-conflicts "
        "(requires claude-code) and have no effective_backend_map entry to override "
        "the raw backend_name='codex'. Got valid=True."
    )
    backend_compat_errors = [
        s
        for s in result.get("suggestions", [])
        if s.get("rule") == "backend-incompatible-skill" and s.get("severity") == Severity.ERROR
    ]
    assert len(backend_compat_errors) >= 1, (
        "Expected at least one backend-incompatible-skill ERROR for merge-conflict steps"
    )
    assert result.get("dispatch_feasible") is True, (
        f"Expected dispatch_feasible=True (backend_supports_git_write=true prevents "
        f"gate infeasibility), got {result.get('dispatch_feasible')}"
    )
