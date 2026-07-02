"""Server-authoritative ingredient override helpers.

Shared between sibling tool modules (tools_kitchen, tools_recipe) that need
to inject runtime-derived values into recipe ingredient overrides. Each
helper returns a plain dict suitable for merging into the
``ingredient_overrides`` keyword of ``load_and_validate``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from autoskillit.config import BACKEND_CAPABILITY_INGREDIENTS
from autoskillit.core import (
    CAPABILITY_INGREDIENT_TO_SKIP_GUARD,
    CapabilityResolutionDetail,
    get_logger,
)

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend
    from autoskillit.recipe.schema import RecipeStep

logger = get_logger(__name__)


def _backend_capability_overrides(backend: CodingAgentBackend | None) -> dict[str, str]:
    """Return ingredient overrides derived from backend capabilities.

    The ``backend_supports_git_write`` ingredient is resolved from the active
    backend's ``git_metadata_writable`` capability. A ``None`` backend is
    treated as writable (safe default for test/dev contexts where no backend
    is wired).
    """
    git_writable = backend is None or backend.capabilities.git_metadata_writable
    return {"backend_supports_git_write": "true" if git_writable else "false"}


def _provider_aware_capability_overrides(
    backend: CodingAgentBackend | None,
    recipe_name: str,
    config_providers: Any | None,
    recipe_steps: dict[str, RecipeStep] | None,
) -> tuple[dict[str, str], CapabilityResolutionDetail]:
    """Return capability overrides with per-step provider awareness.

    Extends ``_backend_capability_overrides`` by considering per-step provider
    overrides: when the orchestrator backend is not git-writable (e.g. Codex)
    but at least one ``run_skill`` step gated by ``backend_supports_git_write``
    has a provider override that resolves to ``ANTHROPIC_BASE_URL``, the override
    flips to ``"true"`` so those steps survive pruning.

    Any-suffices semantics: a single guarded step with ANTHROPIC_BASE_URL is
    sufficient to flip the capability. This preserves defense-in-depth — the
    runtime ``gate_backend_write`` still fires for any surviving step, and the
    per-step ``_check_backend_compat()`` gate verifies skill backend
    requirements at dispatch time.

    Graceful degradation: when any of ``backend``, ``config_providers``, or
    ``recipe_steps`` is ``None``, or when ``backend.capabilities.anthropic_provider_capable``
    is ``True`` (i.e. Claude Code orchestrator), returns the underlying
    ``_backend_capability_overrides`` result unchanged.

    Returns ``(overrides_dict, resolution_detail)``. Early-return paths
    (claude backend, graceful degradation, baseline already true, no guarded
    steps) return ``CapabilityResolutionDetail.empty(path)`` with no
    resolved-step data. ``detail.resolution_path`` identifies which branch
    was taken.
    """
    base = _backend_capability_overrides(backend)
    if base["backend_supports_git_write"] == "true":
        return base, CapabilityResolutionDetail.empty("baseline_already_true")
    if backend is None or config_providers is None or recipe_steps is None:
        return base, CapabilityResolutionDetail.empty("graceful_degradation")
    if getattr(backend.capabilities, "anthropic_provider_capable", False):
        return base, CapabilityResolutionDetail.empty("claude_backend")

    _guard_refs = frozenset(CAPABILITY_INGREDIENT_TO_SKIP_GUARD.values())
    guarded_step_names: list[str] = []
    for step_name, step in recipe_steps.items():
        skip_when = getattr(step, "skip_when_false", None)
        tool = getattr(step, "tool", None)
        if skip_when in _guard_refs and tool == "run_skill":
            guarded_step_names.append(step_name)

    if not guarded_step_names:
        return base, CapabilityResolutionDetail.empty("no_guarded_steps")

    from autoskillit.server._guards import _resolve_provider_profile  # circular-break

    resolved: list[tuple[str, str, bool]] = []
    any_has_base_url = False
    for step_name in guarded_step_names:
        step = recipe_steps[step_name]
        step_provider = getattr(step, "provider", "") or ""
        profile_name, provider_extras = _resolve_provider_profile(
            step_name,
            recipe_name,
            config_providers,
            step_provider=step_provider,
        )
        has_base_url = bool(
            provider_extras
            and isinstance(provider_extras, dict)
            and "ANTHROPIC_BASE_URL" in provider_extras
        )
        resolved.append((step_name, profile_name or "", has_base_url))
        if has_base_url:
            any_has_base_url = True

    if any_has_base_url:
        base["backend_supports_git_write"] = "true"
        return base, CapabilityResolutionDetail(
            resolved_steps=tuple(resolved),
            bail_step=None,
            resolution_path="any_pass",
        )

    logger.info(
        "capability_override_none_pass",
        resolved_steps=resolved,
        recipe_name=recipe_name,
    )
    return base, CapabilityResolutionDetail(
        resolved_steps=tuple(resolved),
        bail_step=None,
        resolution_path="none_pass",
    )


def _promote_capability_keys(
    config_layer: dict[str, str], session_overrides: dict[str, str]
) -> None:
    """Promote backend capability keys from session overrides into the config layer.

    This ensures capability-derived values win the merge even when user-supplied
    overrides would otherwise clobber them.  Mutates ``config_layer`` in place.
    """
    for key in BACKEND_CAPABILITY_INGREDIENTS:
        if key in session_overrides:
            config_layer[key] = session_overrides[key]
