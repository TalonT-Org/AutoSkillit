"""get_recipe MCP resource and ingredient-inspection helpers."""

from __future__ import annotations

import json
from typing import Any

from autoskillit import __version__
from autoskillit.config import (
    SERVER_AUTHORITATIVE_INGREDIENTS,
    iter_display_categories,
)
from autoskillit.core import (
    ProcessStaleError,
    RecipeLoadError,
    get_logger,
)
from autoskillit.server import mcp
from autoskillit.server._misc import strip_ingredients_only_keys
from autoskillit.server._recipe_delivery import (
    enforce_recipe_resource_response,
    prepare_recipe_delivery_generation,
)

# Late-binding for monkeypatch reach: tests patch
# "autoskillit.server.tools.tools_kitchen.<name>" (the package facade), so
# cross-submodule helpers must be resolved via attribute access on the
# package at call time rather than imported by name into this submodule.
from autoskillit.server.tools import tools_kitchen as _tk_pkg
from autoskillit.server.tools._authority_feedback import build_authority_clobber_warnings
from autoskillit.server.tools._auto_overrides import _compute_effective_backend_map
from autoskillit.server.tools._serve_helpers import (
    _admit_recipe_name,
    build_backend_capabilities_map,
    build_open_kitchen_recipe_payload,
    pop_finalized_recipe_projection,
    render_served_response,
)

logger = get_logger(__name__)


@mcp.resource("recipe://{name}")
def get_recipe(name: str) -> str:
    """Return composed recipe YAML for the orchestrating agent to follow.

    ``$<name>`` or ``/<name>`` denotes an in-session skill invocation. Do not pass
    a skill name to ``open_kitchen``, ``load_recipe``, ``migrate_recipe``, or
    ``recipe://``; those surfaces accept recipe identities only.
    A name defined as both a recipe and a skill is rejected until one artifact
    is renamed.
    """
    from autoskillit.server._state import _get_ctx_or_none  # circular-break

    ctx = _get_ctx_or_none()
    if ctx is None or ctx.recipes is None:
        return json.dumps({"error": "Kitchen not open."})
    try:
        match = _admit_recipe_name(ctx, name)
        _defaults = _tk_pkg.resolve_ingredient_defaults(ctx.project_dir)
        _config_layer = _tk_pkg.build_config_authoritative_layer(_defaults)
        _session_overrides: dict[str, str] = {
            "kitchen_id": ctx.kitchen_id,
            "diagnostics_log_dir": str(_tk_pkg.resolve_log_dir(ctx.config.linux_tracing.log_dir)),
        }
        _raw_recipe = ctx.recipes.load(match.path)
        _effective_backend_map, _backend_origin_map = _compute_effective_backend_map(
            _raw_recipe.steps,
            ctx.backend.name if ctx.backend else None,
            name,
            config_backend=ctx.config.agent_backend,
        )
        _backend_capabilities_map = build_backend_capabilities_map(
            _effective_backend_map, ctx.backend
        )
        _config_default = _tk_pkg.build_config_default_layer(_defaults)
        result = _tk_pkg.serve_recipe(
            ctx,
            name,
            caller_overrides=None,
            config_default=_config_default,
            session_overrides=_session_overrides,
            config_layer=_config_layer,
            resolved_defaults=_defaults,
            effective_backend_map=_effective_backend_map,
            backend_name=ctx.backend.name if ctx.backend else None,
            backend_capabilities_map=_backend_capabilities_map,
            backend_origin_map=_backend_origin_map,
        )
        _resource_finalized_projection = (
            pop_finalized_recipe_projection(result) if result.get("valid", False) else None
        )
    except ProcessStaleError:
        logger.warning("get_recipe_failure", recipe=name, stage="process_stale", exc_info=True)
        return json.dumps({"error": f"Recipe '{name}' composition failed — process stale."})
    except RecipeLoadError as exc:
        return json.dumps({"error": str(exc)})
    except Exception:
        logger.warning("get_recipe_failure", recipe=name, stage="load_and_validate", exc_info=True)
        return json.dumps({"error": f"Recipe '{name}' composition failed."})
    if not result.get("valid", False):
        logger.warning("get_recipe_invalid", recipe=name, errors=result.get("errors", []))
        return json.dumps(
            {
                "error": f"Recipe '{name}' failed validation.",
                "errors": result.get("errors", []),
                "suggestions": result.get("suggestions", []),
            }
        )
    if _resource_finalized_projection is None:
        return json.dumps({"error": f"Recipe '{name}' has no finalized projection."})
    prepared_generation = prepare_recipe_delivery_generation(
        result,
        recipe_name=name,
        tool_ctx=ctx,
        finalized_projection=_resource_finalized_projection,
    )
    finalized = _tk_pkg.finalize_recipe_delivery(
        result,
        surface="get_recipe",
        recipe_name=name,
        tool_ctx=ctx,
        finalized_projection=_resource_finalized_projection,
        flow_generation=prepared_generation.flow_generation,
        canonical_artifact_payload=prepared_generation.canonical_artifact_payload,
        execution_snapshot=prepared_generation.execution_snapshot,
        normalized_compile_key=prepared_generation.normalized_compile_key,
    )
    return enforce_recipe_resource_response(finalized, tool_ctx=ctx)


def _build_tool_category_listing(
    features: dict[str, bool], *, experimental_enabled: bool = False
) -> str:
    """Return a formatted string listing all tool categories."""
    lines = []
    for name, tools in iter_display_categories(
        features, experimental_enabled=experimental_enabled
    ):
        lines.append(f"  {name}: {', '.join(tools)}")
    return "\n".join(lines)


def _check_override_keys(
    overrides: dict[str, str] | None,
    declared: frozenset[str],
    session_keys: set[str],
    config_layer: dict[str, str],
) -> list[str]:
    if not overrides:
        return []
    user_keys = set(overrides.keys()) - session_keys - SERVER_AUTHORITATIVE_INGREDIENTS
    unknown = user_keys - declared
    warnings: list[str] = []
    if unknown:
        warnings.append(
            f"Unknown override keys ignored: {sorted(unknown)}. "
            f"Valid ingredient keys: {sorted(declared)}"
        )
    warnings.extend(build_authority_clobber_warnings(overrides, config_layer))
    return warnings


def _render_ingredients_only_response(
    result: dict[str, Any],
    *,
    declared_ingredients: frozenset[str] | None,
    overrides: dict[str, str] | None,
    session_keys: set[str],
    config_layer: dict[str, str],
) -> str:
    """Build the canonical ingredients-only inspection response."""
    inspection = strip_ingredients_only_keys(
        build_open_kitchen_recipe_payload(result, version=__version__)
    )
    if declared_ingredients is not None:
        warnings = _check_override_keys(
            overrides,
            declared_ingredients,
            session_keys,
            config_layer,
        )
        if warnings:
            inspection["warnings"] = warnings
    from autoskillit.server._state import _get_ctx_or_none  # circular-break

    tool_ctx = _get_ctx_or_none()
    if tool_ctx is not None:
        from autoskillit.server.tools.tools_kitchen._open_kitchen_transition import (  # noqa: E501 # circular-break
            _attach_transition_fields,
        )

        _attach_transition_fields(inspection, tool_ctx, committed=True)
    return render_served_response(inspection)
