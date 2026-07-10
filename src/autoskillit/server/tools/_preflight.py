"""Dispatch-feasibility preflight — shared by open_kitchen and dispatch_food_truck."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autoskillit.hook_registry import HOOK_REGISTRY
from autoskillit.pipeline import ToolContext
from autoskillit.recipe import (
    Recipe,
    build_active_recipe_runtime_snapshot,
    parse_recipe_text,
)


def _get_fix_required_hook_matchers(applicable_guards: frozenset[str]) -> list[str]:
    """Return matchers of fix-required hooks not enforced by the given guard set."""
    return [
        h.matcher
        for h in HOOK_REGISTRY
        if h.codex_status == "fix-required"
        and (
            not h.scripts
            or not frozenset(Path(s).stem for s in h.scripts).issubset(applicable_guards)
        )
    ]


def filter_steps_by_post_prune(
    raw_steps: dict[str, Any],
    post_prune_step_names: list[str],
) -> dict[str, Any]:
    """Return only the steps whose names survived skip_when_false pruning."""
    keep = set(post_prune_step_names)
    return {k: v for k, v in raw_steps.items() if k in keep}


def install_active_recipe_snapshot(
    tool_ctx: Any,
    recipe_candidate: object | None,
    result: dict[str, Any],
    *,
    legacy_steps: dict[str, Any] | None = None,
) -> Recipe:
    """Parse, seal, and atomically install one active recipe runtime view."""
    recipe_obj = (
        recipe_candidate
        if isinstance(recipe_candidate, Recipe)
        else parse_recipe_text(str(result.get("content", "")))
    )
    post_prune_step_names = result.get("post_prune_step_names")
    if not isinstance(post_prune_step_names, list):
        post_prune_step_names = list(recipe_obj.steps)
    filtered_steps = filter_steps_by_post_prune(
        legacy_steps if legacy_steps is not None else recipe_obj.steps,
        post_prune_step_names,
    )
    snapshot = build_active_recipe_runtime_snapshot(
        recipe_obj,
        post_prune_step_names=post_prune_step_names,
        required_packs=result.get("requires_packs", []),
        required_features=result.get("requires_features", []),
        content_hash=result.get("content_hash", ""),
        composite_hash=result.get("composite_hash", ""),
        recipe_version=result.get("recipe_version") or "",
        project_identity=str(tool_ctx.project_dir),
    )
    ToolContext.set_active_recipe_snapshot(tool_ctx, snapshot, legacy_steps=filtered_steps)
    return recipe_obj


def _check_dispatch_feasibility(
    post_prune_step_names: list[str],
    active_recipe_steps: dict[str, Any],
    backend: Any | None,
    config_providers: Any,
    recipe_name: str = "",
) -> str | None:
    """Fail-closed dispatch-feasibility preflight.

    Evaluated at open_kitchen time (and dispatch_food_truck time) to detect
    recipe/backend combinations where HOOK_REGISTRY has fix-required entries
    that the current backend cannot enforce. Returns a JSON error envelope
    string on failure, None on pass.
    """
    if backend is None:
        return None
    if not post_prune_step_names:
        return None

    run_skill_step_names: list[str] = []
    for step_name in post_prune_step_names:
        step = active_recipe_steps.get(step_name)
        if step is not None and getattr(step, "tool", None) == "run_skill":
            run_skill_step_names.append(step_name)

    if not run_skill_step_names:
        return None

    from autoskillit.server._guards import _resolve_provider_profile  # circular-break

    feasible_step_names: list[str] = []
    for step_name in run_skill_step_names:
        step = active_recipe_steps.get(step_name)
        step_provider = getattr(step, "provider", "") or ""
        _profile_name, provider_extras = _resolve_provider_profile(
            step_name,
            recipe_name,
            config_providers,
            step_provider=step_provider,
        )
        if (
            provider_extras
            and "ANTHROPIC_BASE_URL" in provider_extras
            and not backend.capabilities.anthropic_provider_capable
        ):
            continue
        feasible_step_names.append(step_name)

    if not feasible_step_names:
        return None

    fix_required_matchers = _get_fix_required_hook_matchers(
        backend.capabilities.applicable_guards,
    )
    if not fix_required_matchers:
        return None

    return json.dumps(
        {
            "success": False,
            "kitchen": "preflight_failed",
            "user_visible_message": (
                f"Cannot dispatch recipe: backend {backend.name!r} cannot enforce "
                f"HOOK_REGISTRY fix-required entries "
                f"[{', '.join(fix_required_matchers)}]. "
                f"Add a per-step provider override that sets ANTHROPIC_BASE_URL "
                f"to reroute dispatch to claude-code, which has full hook enforcement."
            ),
            "error": (
                f"Dispatch infeasible on backend {backend.name!r}: "
                f"fix-required hooks {fix_required_matchers} are not enforceable."
            ),
            "stage": "dispatch_feasibility_preflight",
            "unfixable_matchers": fix_required_matchers,
            "backend": backend.name,
            "escape_hatch": (
                "Per-step provider: override with ANTHROPIC_BASE_URL set to "
                "reroute dispatch to claude-code (which enforces all fix-required hooks)."
            ),
        }
    )
