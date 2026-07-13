"""Dispatch-feasibility preflight — shared by open_kitchen and dispatch_food_truck."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from autoskillit.hook_registry import HOOK_REGISTRY


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


def _check_dispatch_feasibility(
    post_prune_step_names: list[str],
    active_recipe_steps: dict[str, Any],
    backend: Any | None,
    config_providers: Any,
    recipe_name: str = "",
    *,
    config_backend: Any | None = None,
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

    from autoskillit.server._guards import (  # circular-break
        _resolve_backend_override,
        _resolve_provider_profile,
    )

    feasible_step_names: list[str] = []
    for step_name in run_skill_step_names:
        # Explicit config backend override takes precedence over capability
        # routing for preflight too (mirrors the dispatch path):
        # - pinned to claude-code: feasible regardless of orchestrator backend
        #   (effective backend enforces all fix-required hooks).
        # - pinned to a non-claude backend: only feasible if its required
        #   binary is on PATH; otherwise the step cannot run, so exclude it
        #   from the feasibility check (preflight won't fire on its account).
        if config_backend is not None:
            _explicit = _resolve_backend_override(step_name, recipe_name, config_backend)
            if _explicit is not None:
                from autoskillit.execution.backends import get_backend

                _explicit_obj = get_backend(_explicit)
                _explicit_binary = getattr(_explicit_obj.capabilities, "process_name", "")
                if _explicit_binary and shutil.which(_explicit_binary) is None:
                    continue
                if getattr(_explicit_obj.capabilities, "anthropic_provider_capable", False):
                    continue

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
