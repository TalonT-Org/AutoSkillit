"""Recipe-tool formatters and field-coverage contracts."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from _fmt_primitives import (  # type: ignore[import-not-found]
    _CHECK_MARK,
    _CROSS_MARK,
    _RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    _WARN_MARK,
)
from _fmt_recipe_compact import (  # type: ignore[import-not-found]
    compact_orchestration_rules,
    compact_recipe_display,
)

if TYPE_CHECKING:
    from autoskillit._recipe_delivery_framing import is_attested_recipe_delivery
    from autoskillit.recipe import ListRecipesResult, LoadRecipeResult, OpenKitchenResult
else:
    from _recipe_delivery_framing import is_attested_recipe_delivery


_RECIPE_INITIALIZATION_FIELDS: tuple[str, ...] = (
    "flow_records",
    "recipe_execution",
    "recipe_flow",
    "recipe_pull",
    "delivery_bound_spill",
    "initialization_id",
    "recovery",
    "required_sections",
)

_FMT_LOAD_RECIPE_RENDERED: frozenset[str] = frozenset(
    {
        "valid",
        "suggestions",
        "content",
        "summary",
        "diagram",
        "ingredients_table",
        "orchestration_rules",
        "warnings",
        *_RECIPE_INITIALIZATION_FIELDS,
    }
)
_FMT_LOAD_RECIPE_SUPPRESSED: frozenset[str] = frozenset(
    {
        "greeting",  # delivered via positional CLI arg, not MCP response
        "errors",  # structural validation errors; internal to load_and_validate
        "kitchen_rules",  # already in the YAML content
        "requires_packs",  # internal field; used for skill gating, not display
        "requires_features",  # internal feature gate enablement field
        "content_hash",  # internal identity metadata
        "composite_hash",  # internal identity metadata
        "recipe_version",  # internal identity metadata
        "stop_step_semantics",  # delivered via open_kitchen response Channel B; not redisplayed
        "deferred_guards",  # internal deferral metadata; not displayed to agent
        "post_prune_step_names",  # internal preflight field; not displayed to agent
        "post_prune_routing_edges",  # internal preflight field; not displayed to agent
        "_finalized_projection",  # internal host-attested finalized recipe carrier
    }
)

# Derived displays strip their source block to avoid duplicate rendering.
_LOAD_RECIPE_CONTENT_DERIVED_FROM: dict[str, str] = {
    "ingredients_table": "content",  # GFM table derived from the ingredients: block in content
}


def _strip_yaml_ingredients_block(yaml_text: str) -> str:
    """Remove the top-level ``ingredients`` block from YAML text."""
    lines = yaml_text.splitlines(keepends=True)
    result: list[str] = []
    in_ingredients = False
    for line in lines:
        if line.startswith("ingredients:"):
            in_ingredients = True
            continue
        if in_ingredients:
            if line and not line[0].isspace():
                in_ingredients = False
                result.append(line)
        else:
            result.append(line)
    return "".join(result)


def _fmt_recipe_segment(carrier: object) -> str:
    """Render an inseparable startup, success, or recovery carrier."""
    if not isinstance(carrier, Mapping):
        return ""
    rendered = json.dumps(
        dict(carrier),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"--- RECIPE SEGMENT ---\n{rendered}\n--- END RECIPE SEGMENT ---"


def _fmt_recipe_body(data: Mapping[str, Any]) -> list[str]:
    """Render flow before content so bounded heads preserve step ordering."""
    lines: list[str] = []
    summary = data.get("summary")
    if summary:
        lines.append("\n--- STEP FLOW ---")
        lines.append(" ".join(str(summary).split()))
        lines.append("--- END STEP FLOW ---")
    diagram = data.get("diagram")
    if diagram:
        lines.append("\n--- FLOW DIAGRAM ---")
        lines.append(diagram)
        lines.append("--- END DIAGRAM ---")
    content = data.get("content")
    if content:
        display_content = content
        for derived_field in _LOAD_RECIPE_CONTENT_DERIVED_FROM:
            if data.get(derived_field):
                display_content = _strip_yaml_ingredients_block(display_content)
        display_content = compact_recipe_display(display_content)
        lines.append("\n--- RECIPE ---")
        lines.append(display_content)
        lines.append("--- END RECIPE ---")
    initialization = {
        key: data[key] for key in _RECIPE_INITIALIZATION_FIELDS if data.get(key) is not None
    }
    if initialization:
        lines.append("\n--- RECIPE INITIALIZATION ---")
        lines.append(
            json.dumps(
                initialization,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        lines.append("--- END RECIPE INITIALIZATION ---")
    ing_table = data.get("ingredients_table")
    if ing_table:
        lines.append("\n--- INGREDIENTS TABLE (display this verbatim to the user) ---")
        lines.append(ing_table)
        lines.append("--- END TABLE ---")
    suggestions = data.get("suggestions") or []
    errors = [
        f for f in suggestions if isinstance(f, dict) and f.get("severity") in ("error", "warning")
    ]
    if errors:
        lines.append(f"\n{len(errors)} finding(s)")
    orch_rules = data.get("orchestration_rules")
    if orch_rules:
        lines.append(f"\n{compact_orchestration_rules(orch_rules)}")
    warnings = data.get("warnings") or []
    for warning in warnings:
        lines.append(f"\n{_WARN_MARK} {warning}")
    return lines


def _fmt_load_recipe(data: LoadRecipeResult, pipeline: bool) -> str:
    """Format load_recipe result as Markdown-KV."""
    if not isinstance(data, dict):
        return "## load_recipe\n\n_(unexpected response type)_"

    error = data.get("error")
    if error:
        return f"## load_recipe {_CROSS_MARK}\n\n**Error:** {error}"

    valid = data.get("valid", True)
    mark = _CHECK_MARK if valid else _CROSS_MARK
    lines: list[str] = [f"## load_recipe {mark}"]
    lines.extend(_fmt_recipe_body(data))
    return "\n".join(lines)


_FMT_LIST_RECIPES_RENDERED: frozenset[str] = frozenset(
    {
        "recipes",
        "count",
        "errors",
    }
)
_FMT_LIST_RECIPES_SUPPRESSED: frozenset[str] = frozenset()

_FMT_RECIPE_LIST_ITEM_RENDERED: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "summary",
        "source",
    }
)
_FMT_RECIPE_LIST_ITEM_SUPPRESSED: frozenset[str] = frozenset()

_FMT_OPEN_KITCHEN_RENDERED: frozenset[str] = frozenset(
    {
        "valid",
        "suggestions",
        "content",
        "summary",
        "diagram",
        "ingredients_table",
        "orchestration_rules",
        "version",
        "warnings",
        "kitchen_id",
        "operation_id",
        "intent_fingerprint",
        "received_intent_fingerprint",
        "phase",
        "effects",
        "degraded_evidence",
        "ambiguity",
        "retry_disposition",
        "recipe_segment",
        *_RECIPE_INITIALIZATION_FIELDS,
    }
)
_FMT_OPEN_KITCHEN_SUPPRESSED: frozenset[str] = frozenset(
    {
        "success",  # metadata — model infers success from formatted output
        "kitchen",  # metadata — model knows kitchen state from context
        "errors",  # structural validation errors; internal to load_and_validate
        "greeting",  # delivered via CLI preview, not MCP response
        "kitchen_rules",  # already embedded in YAML content
        "requires_packs",  # internal gating field
        "requires_features",  # internal feature gate enablement field
        "content_hash",  # internal identity metadata
        "composite_hash",  # internal identity metadata
        "recipe_version",  # internal identity metadata
        "stop_step_semantics",  # delivered via open_kitchen Channel B
        "hook_warning",  # edge-case diagnostic; not rendered in standard path
        "deferred_guards",  # internal deferral metadata; not displayed to agent
        "post_prune_step_names",  # internal preflight field; not displayed to agent
        "post_prune_routing_edges",  # internal preflight field; not displayed to agent
    }
)


def _fmt_kitchen_transition(data: OpenKitchenResult) -> list[str]:
    """Preserve replay and reconciliation authority in pretty output."""
    lines: list[str] = []
    for field in (
        "kitchen_id",
        "operation_id",
        "intent_fingerprint",
        "received_intent_fingerprint",
        "phase",
        "retry_disposition",
    ):
        value = data.get(field)
        if value not in (None, ""):
            lines.append(f"{field}: {value}")
    for field in ("effects", "degraded_evidence", "ambiguity"):
        value = data.get(field)
        if value:
            lines.append(f"{field}: {value!r}")
    return lines


def _fmt_open_kitchen(data: OpenKitchenResult, pipeline: bool) -> str:
    """Format open_kitchen combined kitchen+recipe result."""
    version = data.get("version", "")

    error = data.get("error")
    if error:
        error_lines = [
            f"## open_kitchen {_CROSS_MARK} v{version}",
            f"\nKitchen open. Recipe error: {error}",
        ]
        error_lines.extend(_fmt_kitchen_transition(data))
        return "\n".join(error_lines)

    valid = data.get("valid", True)
    mark = _CHECK_MARK if valid else _CROSS_MARK
    lines: list[str] = [f"## open_kitchen {mark} v{version}"]
    lines.extend(_fmt_kitchen_transition(data))
    lines.extend(_fmt_recipe_body(data))
    formatted_segment = _fmt_recipe_segment(data.get("recipe_segment"))
    if formatted_segment:
        lines.extend(("", formatted_segment))
    formatted = "\n".join(lines)

    byte_len = len(formatted.encode("utf-8"))
    budget = _RESPONSE_BACKSTOP_EXEMPTION_REGISTRY["open_kitchen"]["max_utf8_bytes"]
    if byte_len > budget:
        content_hash = data.get("content_hash", "")
        print(
            f"open_kitchen payload over budget: content_hash={content_hash} "
            f"bytes={byte_len} budget={budget}",
            file=sys.stderr,
        )
    return formatted


def _fmt_open_kitchen_plain_text(text: str, _pipeline: bool) -> str:
    """Format open_kitchen plain-text response (no recipe attached)."""
    if is_attested_recipe_delivery(text):
        return text
    return f"## open_kitchen\n\n{text}"


def _fmt_list_recipes(data: ListRecipesResult, pipeline: bool) -> str:
    """Format list_recipes result as Markdown-KV."""
    if not isinstance(data, dict):
        return "## list_recipes\n\n_(unexpected response type)_"
    lines: list[str] = ["## list_recipes"]
    recipes = data.get("recipes") or []
    for recipe in recipes[:30]:
        if isinstance(recipe, dict):
            name = recipe.get("name", "?")
            desc = recipe.get("description", "")
            summary = recipe.get("summary", "")
            source = recipe.get("source", "")
            source_tag = f" [{source}]" if source else ""
            lines.append(f"  - {name}{source_tag}: {desc}" if desc else f"  - {name}{source_tag}")
            if summary:
                lines.append(f"    {summary}")
        else:
            lines.append(f"  - {recipe}")
    if len(recipes) > 30:
        lines.append(f"  ... and {len(recipes) - 30} more")
    count = data.get("count", len(recipes))
    lines.append(f"\n{count} recipe(s) available")
    errors = data.get("errors") or []
    if errors:
        lines.append(f"\n{_WARN_MARK} {len(errors)} recipe file(s) had load errors")
    return "\n".join(lines)
