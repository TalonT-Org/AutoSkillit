"""Bounded wire rendering for recipe-section tool failures."""

from __future__ import annotations

from collections.abc import Mapping

from autoskillit.core import (
    RECIPE_SECTION_MANDATORY_FAILURE_CODES,
    canonical_recipe_section_json,
)


def render_recipe_section_failure(
    code: str,
    *,
    bound_bytes: int,
    context: Mapping[str, object] | None = None,
) -> str:
    """Render one registered atomic failure, dropping context before truncation."""
    if code not in RECIPE_SECTION_MANDATORY_FAILURE_CODES:
        raise ValueError(f"unregistered recipe section failure code: {code}")

    base: dict[str, object] = {"error": code, "success": False}
    if context:
        candidate = dict(base)
        candidate.update(
            {name: value for name, value in context.items() if name not in {"error", "success"}}
        )
        rendered = canonical_recipe_section_json(candidate)
        if len(rendered.encode("utf-8")) <= bound_bytes:
            return rendered
    return canonical_recipe_section_json(base)
