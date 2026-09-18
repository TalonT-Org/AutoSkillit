"""Canonical recipe step note templates.

Single source for the prose blocks that multiple bundled recipes would
otherwise duplicate verbatim. Bundled YAML files reference these via the
placeholder string ``RECIPE_NOTE_ANALYZE_PIPELINE_HEALTH_ERROR`` (a value
chosen to be highly unlikely to appear in legitimate recipe content).

The runtime YAML loader (``load_recipe_dict_with_declarations``) and the
recipe pre-compiler (``scripts/compile_recipes.py``) both substitute the
placeholder with the corresponding constant below.
"""

from __future__ import annotations

__all__ = [
    "ANALYZE_PIPELINE_HEALTH_ERROR_NOTE",
    "RECIPE_NOTE_ANALYZE_PIPELINE_HEALTH_ERROR",
]


ANALYZE_PIPELINE_HEALTH_ERROR_NOTE: str = (
    "Preserve the originating failure response separately. Inspect the diagnostic "
    "response and include its non-empty result verbatim as supplementary evidence. "
    "On on_failure, on_context_limit, or on_rate_limit, state that diagnostic evidence "
    "was unavailable and reproduce only observed tool evidence; do not infer a cause. "
    "When pipeline_health disables this step and it was not run, say so and preserve "
    "the originating failure response."
)


RECIPE_NOTE_ANALYZE_PIPELINE_HEALTH_ERROR: str = "__RECIPE_NOTE_ANALYZE_PIPELINE_HEALTH_ERROR__"


_RECIPE_NOTE_TABLE: dict[str, str] = {
    RECIPE_NOTE_ANALYZE_PIPELINE_HEALTH_ERROR: ANALYZE_PIPELINE_HEALTH_ERROR_NOTE,
}


def substitute_recipe_note_placeholders(value: object) -> object:
    """Recursively substitute recipe note placeholders inside any nested structure."""
    if isinstance(value, dict):
        return {k: substitute_recipe_note_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute_recipe_note_placeholders(item) for item in value]
    if isinstance(value, str):
        return _RECIPE_NOTE_TABLE.get(value, value)
    return value
