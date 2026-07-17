"""Dispatch-feasibility preflight — shared by open_kitchen and dispatch_food_truck."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    CodingAgentBackend,
    describe_capability_mismatches,
    unsatisfied_backend_capabilities,
)
from autoskillit.hook_registry import HOOK_REGISTRY
from autoskillit.server._misc import get_backend

if TYPE_CHECKING:
    from autoskillit.config._config_dataclasses import AgentBackendConfig
    from autoskillit.core import SkillResolver


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


def check_hard_capability_feasibility(
    uses_capabilities: frozenset[str],
    backend: CodingAgentBackend,
) -> str | None:
    """Return diagnostic string if any capability's required_backend_property is unsatisfied.

    Delegates registry lookup and BackendCapabilities evaluation to the shared
    core predicate, then formats the first mismatch for server callers. Returns
    None when every capability is satisfied.
    """
    mismatches = unsatisfied_backend_capabilities(uses_capabilities, backend.capabilities)
    if mismatches:
        return f"Backend '{backend.name}': {describe_capability_mismatches(mismatches)}."
    return None


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
    config_backend: AgentBackendConfig | None = None,
    skill_resolver: SkillResolver | None,
) -> str | None:
    """Fail-closed dispatch-feasibility preflight.

    Evaluated at open_kitchen time (and dispatch_food_truck time) to detect
    recipe/backend combinations where HOOK_REGISTRY has fix-required entries
    that the current backend cannot enforce. Returns a JSON error envelope
    string on failure, None on pass.

    `skill_resolver` is REQUIRED for explicit-pin hard-capability feasibility
    checks. When omitted, the function fails closed for any pinned step: the
    dispatch is refused as infeasible rather than silently bypassing the gate.
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
        # - pinned to a non-claude backend: subject to hard-capability
        #   feasibility check — if the pinned backend lacks a required
        #   BackendCapabilities property (e.g. git_metadata_writable=False
        #   for git_metadata_write skills), dispatch is infeasible and the
        #   gate refuses the recipe.
        if config_backend is not None:
            _resolution = _resolve_backend_override(step_name, recipe_name, config_backend)
            if _resolution is not None:
                _explicit = _resolution.backend
                try:
                    _pinned_backend = get_backend(_explicit)
                except (ValueError, KeyError):
                    continue
                # Hard-capability feasibility: when an explicit pin targets a
                # backend that lacks a BackendCapabilities property required
                # by the step's skill (REQ-RES-001 suppression is structural,
                # not opaque — capability feasibility still applies), refuse
                # dispatch.
                if skill_resolver is None:
                    return json.dumps(
                        {
                            "success": False,
                            "kitchen": "preflight_failed",
                            "user_visible_message": (
                                f"Cannot verify capability feasibility for explicitly-pinned "
                                f"step '{step_name}': skill resolver is not available."
                            ),
                            "error": "skill_resolver_unavailable_for_pinned_step",
                            "stage": "dispatch_feasibility_preflight",
                            "step": step_name,
                        }
                    )
                _step_obj = active_recipe_steps.get(step_name)
                _skill_name = (
                    getattr(_step_obj, "skill_name", None) if _step_obj is not None else None
                )
                if _skill_name:
                    _skill_info = skill_resolver.resolve(_skill_name)
                    if _skill_info is None:
                        return json.dumps(
                            {
                                "success": False,
                                "kitchen": "preflight_failed",
                                "user_visible_message": (
                                    f"Cannot verify capability feasibility for explicitly-pinned "
                                    f"step '{step_name}': skill '{_skill_name}' could not be "
                                    "resolved."
                                ),
                                "error": "skill_not_found_for_pinned_step",
                                "stage": "dispatch_feasibility_preflight",
                                "step": step_name,
                                "skill": _skill_name,
                            }
                        )
                    _skill_caps: frozenset[str] = getattr(
                        _skill_info, "uses_capabilities", frozenset()
                    )
                    if _skill_caps:
                        hard_cap_err = check_hard_capability_feasibility(
                            _skill_caps, _pinned_backend
                        )
                        if hard_cap_err:
                            return json.dumps(
                                {
                                    "success": False,
                                    "kitchen": "preflight_failed",
                                    "user_visible_message": (
                                        f"Cannot dispatch step '{step_name}': explicitly "
                                        f"pinned to backend '{_explicit}' which lacks required "
                                        f"capability. {hard_cap_err}"
                                    ),
                                    "error": hard_cap_err,
                                    "stage": "dispatch_feasibility_preflight",
                                    "backend": _explicit,
                                    "step": step_name,
                                    "origin": _resolution.key_path,
                                    "remedy": (
                                        f"Remove or change '{_resolution.key_path}' in "
                                        "~/.autoskillit/config.yaml or "
                                        "<project>/.autoskillit/config.yaml, or pin a "
                                        "backend with the required capability."
                                    ),
                                }
                            )
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
