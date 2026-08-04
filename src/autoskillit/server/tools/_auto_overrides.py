"""Explicit per-step backend authority mapping for recipe execution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from autoskillit.core import SKILL_TOOLS

if TYPE_CHECKING:
    from autoskillit.config._config_dataclasses import AgentBackendConfig
    from autoskillit.recipe.schema import RecipeStep


def _compute_effective_backend_map(
    recipe_steps: dict[str, RecipeStep] | None,
    backend_name: str | None,
    recipe_name: str,
    *,
    config_backend: AgentBackendConfig | None = None,
) -> tuple[dict[str, str] | None, dict[str, str]]:
    """Build the per-step backend map from explicit authorities only."""
    if backend_name is None or recipe_steps is None:
        return None, {}

    result: dict[str, str] = {}
    origin_map: dict[str, str] = {}
    for step_name, step in recipe_steps.items():
        if getattr(step, "tool", None) not in SKILL_TOOLS:
            continue
        if config_backend is not None:
            from autoskillit.server._guards import (  # circular-break: server context bootstrap
                _resolve_backend_override,
            )

            explicit = _resolve_backend_override(step_name, recipe_name, config_backend)
            if explicit is not None:
                result[step_name] = explicit.backend
                origin_map[step_name] = explicit.key_path
                continue
        result[step_name] = backend_name
    return (result if result else None), origin_map
