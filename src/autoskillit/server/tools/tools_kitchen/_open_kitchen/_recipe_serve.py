"""Named-recipe serving: shared setup plus the deferred-recall/normal-serve pair.

Cross-submodule helpers are imported directly from their submodules
(``.._open_kitchen_transition``, ``.._open_kitchen_errors``,
``.._get_recipe``, ``.._tracker_auto_init``) to avoid a circular-import
hazard through the package facade.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from fastmcp import Context

from autoskillit import __version__
from autoskillit.core import (
    FinalizedRecipeProjection,
    ProcessStaleError,
    RecipeDeliveryRequest,
    get_logger,
)
from autoskillit.pipeline import KITCHEN_EFFECT_RECIPE_SERVING, ToolContext, transition_abort
from autoskillit.server.recipe._recipe_delivery import prepare_recipe_delivery_generation
from autoskillit.server.tools import tools_kitchen as _tk_pkg
from autoskillit.server.tools._auto_overrides import _compute_effective_backend_map
from autoskillit.server.tools._serve_helpers import (
    build_backend_capabilities_map,
    build_open_kitchen_recipe_payload,
    pop_finalized_recipe_projection,
)
from autoskillit.server.tools._type_coercion import _validate_override_types
from autoskillit.server.tools._types import _validate_result
from autoskillit.server.tools.tools_kitchen._get_recipe import (
    _check_override_keys,
    _render_ingredients_only_response,
)
from autoskillit.server.tools.tools_kitchen._open_kitchen._tracker_auto_init import (  # circular-break  # noqa: E501
    _auto_init_pipeline_tracker,
    _pipeline_tracker_auto_init_failure,
    prune_stale_kitchen_state,
)
from autoskillit.server.tools.tools_kitchen._open_kitchen_errors import (
    _kitchen_failure_envelope,
    _recipe_validation_error_response,
)
from autoskillit.server.tools.tools_kitchen._open_kitchen_transition import (
    _attach_transition_fields,
    _transition_start,
)

if TYPE_CHECKING:
    from autoskillit.recipe import RecipeInfo

logger = get_logger(__name__)


def _cache_finalized_recipe_projection(
    tool_ctx: ToolContext,
    projection: FinalizedRecipeProjection,
) -> None:
    """Install the finalized execution graph as the active runtime authority."""
    tool_ctx.active_recipe_projection = projection
    tool_ctx.active_recipe_steps = {step.name: step for step in projection.ordered_steps}
    tool_ctx.active_recipe_ingredients = projection.ingredient_names


def _clear_active_recipe_projection(tool_ctx: ToolContext) -> None:
    """Prevent a failed serve from retaining prior execution-graph authority."""
    tool_ctx.active_recipe_projection = None
    tool_ctx.active_recipe_steps = {}
    tool_ctx.active_recipe_ingredients = frozenset()


async def _preflight_named_recipe(
    ctx: Context,
    tool_ctx: ToolContext,
    name: str,
    projection: FinalizedRecipeProjection,
    *,
    prune_stale: bool,
) -> str | None:
    if tool_ctx.active_recipe_steps is None:
        return None
    if prune_stale:
        try:
            prune_stale_kitchen_state(tool_ctx.project_dir, tool_ctx.kitchen_id)
        except Exception:
            logger.warning("open_kitchen_deferred_prune_failed", exc_info=True)
    tracker_error = _auto_init_pipeline_tracker(tool_ctx)
    if tracker_error is not None:
        _clear_active_recipe_projection(tool_ctx)
        return _pipeline_tracker_auto_init_failure(tool_ctx, tracker_error)
    preflight_error = _tk_pkg._check_dispatch_feasibility(
        post_prune_step_names=list(projection.ordered_step_names),
        active_recipe_steps=tool_ctx.active_recipe_steps,
        backend=tool_ctx.backend,
        config_providers=tool_ctx.config.providers,
        recipe_name=name,
        config_backend=tool_ctx.config.agent_backend,
        skill_resolver=tool_ctx.skill_resolver,
        project_root=tool_ctx.project_dir,
        temp_dir=tool_ctx.temp_dir,
    )
    if preflight_error is not None:
        _clear_active_recipe_projection(tool_ctx)
        transition_abort(tool_ctx, KITCHEN_EFFECT_RECIPE_SERVING)
        tool_ctx.gate.disable()
        tool_ctx.gate_infrastructure_ready = False
        await ctx.disable_components(tags={"kitchen"})
        return preflight_error
    return None


def _finalize_named_recipe_delivery(
    result: dict[str, Any],
    *,
    name: str,
    tool_ctx: ToolContext,
    projection: FinalizedRecipeProjection,
    surface: str,
    delivery_request: RecipeDeliveryRequest | None,
) -> str:
    prepared_generation = prepare_recipe_delivery_generation(
        result,
        recipe_name=name,
        tool_ctx=tool_ctx,
        finalized_projection=projection,
    )
    _attach_transition_fields(result, tool_ctx, committed=True)
    return cast(
        str,
        _tk_pkg.finalize_recipe_delivery(
            result,
            surface=surface,
            recipe_name=name,
            tool_ctx=tool_ctx,
            finalized_projection=projection,
            flow_generation=prepared_generation.flow_generation,
            canonical_artifact_payload=prepared_generation.canonical_artifact_payload,
            execution_snapshot=prepared_generation.execution_snapshot,
            normalized_compile_key=prepared_generation.normalized_compile_key,
            delivery_request=delivery_request,
        ),
    )


async def _serve_named_recipe(
    ctx: Context,
    name: str,
    overrides: dict[str, str] | None,
    ingredients_only: bool,
    delivery_request: RecipeDeliveryRequest | None,
    admitted_recipe_info: RecipeInfo | None,
    is_deferred_recall: bool,
) -> str:
    """Serve a named recipe: shared setup, then the deferred-recall/normal-serve pair."""
    from autoskillit.server import _get_ctx  # circular-break

    tool_ctx = _get_ctx()
    if not ingredients_only:
        _tk_pkg.clear_recipe_execution(tool_ctx)
    if tool_ctx.recipes is None:
        return _kitchen_failure_envelope(
            RuntimeError("Server not initialized"),
            stage="recipe_context",
            user_hint=(
                "open_kitchen cannot load a recipe because the server is not "
                "initialized. Run 'autoskillit doctor' to diagnose."
            ),
        )
    suppressed = tool_ctx.config.migration.suppressed
    _defaults = _tk_pkg.resolve_ingredient_defaults(tool_ctx.project_dir)
    assert admitted_recipe_info is not None
    _recipe_info = admitted_recipe_info
    _raw_recipe = tool_ctx.recipes.load(_recipe_info.path)
    _session_overrides: dict[str, str] = {
        "kitchen_id": tool_ctx.kitchen_id,
        "diagnostics_log_dir": str(_tk_pkg.resolve_log_dir(tool_ctx.config.linux_tracing.log_dir)),
    }
    _config_layer = _tk_pkg.build_config_authoritative_layer(_defaults)
    _config_default = _tk_pkg.build_config_default_layer(_defaults)
    _effective_backend_map, _backend_origin_map = _compute_effective_backend_map(
        _raw_recipe.steps if _raw_recipe is not None else None,
        tool_ctx.backend.name if tool_ctx.backend else None,
        name,
        config_backend=tool_ctx.config.agent_backend,
    )
    _backend_capabilities_map = build_backend_capabilities_map(
        _effective_backend_map, tool_ctx.backend
    )
    # Runtime enum check: output_mode must be validated before recipe loading
    if name == "research":
        _om_value = (overrides or {}).get("output_mode")
        if _om_value is not None and _om_value not in {"pr", "local"}:
            return json.dumps(
                {
                    "error": (
                        f"output_mode must be 'pr' or 'local', got {_om_value!r}. "
                        "Only two modes are supported for the research recipe."
                    )
                }
            )
    if overrides and _raw_recipe is None:
        return _kitchen_failure_envelope(
            RuntimeError("recipe failed to load"), stage="ingredient_type_validation"
        )
    if _t := _validate_override_types(overrides, _raw_recipe):
        return _t

    try:
        _transition_start(tool_ctx, KITCHEN_EFFECT_RECIPE_SERVING)
        result = _tk_pkg.serve_recipe(
            tool_ctx,
            name,
            caller_overrides=overrides,
            config_default=_config_default,
            session_overrides=_session_overrides,
            config_layer=_config_layer,
            resolved_defaults=_defaults,
            suppressed=suppressed,
            backend_name=tool_ctx.backend.name if tool_ctx.backend else None,
            effective_backend_map=_effective_backend_map,
            backend_capabilities_map=_backend_capabilities_map,
            backend_origin_map=_backend_origin_map,
        )
        _finalized_projection = (
            pop_finalized_recipe_projection(result) if result.get("valid", False) else None
        )
    except ProcessStaleError as exc:
        _clear_active_recipe_projection(tool_ctx)
        logger.warning("open_kitchen_failure", stage="process_stale", exc_info=True)
        return _kitchen_failure_envelope(exc, stage="process_stale")
    except Exception as exc:
        _clear_active_recipe_projection(tool_ctx)
        logger.warning("open_kitchen_failure", stage="load_and_validate", exc_info=True)
        return _kitchen_failure_envelope(exc, stage="load_and_validate")
    if ingredients_only:
        if not result.get("valid", False):
            transition_abort(tool_ctx, KITCHEN_EFFECT_RECIPE_SERVING)
        return _render_ingredients_only_response(
            result,
            declared_ingredients=(
                frozenset(_raw_recipe.ingredients) if _raw_recipe else frozenset()
            ),
            overrides=overrides,
            session_keys=set(_session_overrides),
            recipe_obj=_raw_recipe,
        )

    if is_deferred_recall:
        tool_ctx.active_recipe_packs = frozenset(result.get("requires_packs", []))
        tool_ctx.active_recipe_features = frozenset(result.get("requires_features", []))
        tool_ctx.recipe_content_hash = result.get("content_hash", "")
        tool_ctx.recipe_composite_hash = result.get("composite_hash", "")
        tool_ctx.recipe_version = result.get("recipe_version") or ""
        recipe_info = _recipe_info
        # Default to False for missing 'valid' so a absent key is treated as invalid
        if not result.get("valid", False) or not result.get("content", ""):
            _clear_active_recipe_projection(tool_ctx)
            transition_abort(tool_ctx, KITCHEN_EFFECT_RECIPE_SERVING)
            tool_ctx.gate.disable()
            tool_ctx.gate_infrastructure_ready = False
            return _recipe_validation_error_response(name, result)
        if _finalized_projection is None:
            _clear_active_recipe_projection(tool_ctx)
            return _recipe_validation_error_response(name, result)
        _cache_finalized_recipe_projection(tool_ctx, _finalized_projection)
        if (
            preflight_error := await _preflight_named_recipe(
                ctx, tool_ctx, name, _finalized_projection, prune_stale=False
            )
        ) is not None:
            return preflight_error
        result = build_open_kitchen_recipe_payload(result, version=__version__)
        try:
            result = await _tk_pkg._apply_triage_gate(result, name, recipe_info=recipe_info)
        except Exception as exc:
            _clear_active_recipe_projection(tool_ctx)
            logger.warning("open_kitchen_failure", stage="apply_triage_gate", exc_info=True)
            return _kitchen_failure_envelope(exc, stage="apply_triage_gate")
        _override_warnings = _check_override_keys(
            overrides,
            _finalized_projection.ingredient_names,
            set(_session_overrides.keys()),
        )
        if _override_warnings:
            result["warnings"] = _override_warnings
        # When caller provides explicit overrides, update the snapshot so
        # subsequent load_recipe/get_recipe calls see the new overrides.
        # When overrides=None (replay previous context), leave the existing
        # snapshot intact — the caller's intent is continuity, not reset.
        if overrides is not None:
            tool_ctx.session_serve_overrides = dict(overrides)
            tool_ctx.session_serve_defer_unresolved = not bool(overrides)
        return _finalize_named_recipe_delivery(
            result,
            name=name,
            tool_ctx=tool_ctx,
            projection=_finalized_projection,
            surface="open_kitchen_deferred_recall",
            delivery_request=delivery_request,
        )

    tool_ctx.active_recipe_packs = frozenset(result.get("requires_packs", []))
    tool_ctx.active_recipe_features = frozenset(result.get("requires_features", []))
    tool_ctx.recipe_name = name
    tool_ctx.recipe_content_hash = result.get("content_hash", "")
    tool_ctx.recipe_composite_hash = result.get("composite_hash", "")
    tool_ctx.recipe_version = result.get("recipe_version") or ""

    try:
        from autoskillit.server.tools.tools_kitchen import (  # circular-break
            _update_hook_config_with_git_ops_policy,
            _update_hook_config_with_recipe,
        )

        _update_hook_config_with_recipe()
        _update_hook_config_with_git_ops_policy()
    except Exception:
        logger.warning("open_kitchen_failure", stage="update_hook_config", exc_info=True)

    composite = result.get("composite_hash", "")
    from autoskillit.server.lifecycle._state import _check_rerun  # circular-break

    rerun_suggestion = _check_rerun(tool_ctx.config.linux_tracing.log_dir, composite)
    if rerun_suggestion:
        result.setdefault("suggestions", []).append(rerun_suggestion)

    recipe_info = _recipe_info

    try:
        result = await _tk_pkg._apply_triage_gate(result, name, recipe_info=recipe_info)
    except Exception as exc:
        _clear_active_recipe_projection(tool_ctx)
        logger.warning("open_kitchen_failure", stage="apply_triage_gate", exc_info=True)
        return _kitchen_failure_envelope(exc, stage="apply_triage_gate")

    if not result.get("valid", False) or not result.get("content", ""):
        _clear_active_recipe_projection(tool_ctx)
        transition_abort(tool_ctx, KITCHEN_EFFECT_RECIPE_SERVING)
        tool_ctx.gate.disable()
        tool_ctx.gate_infrastructure_ready = False
        return _recipe_validation_error_response(name, result)
    if _finalized_projection is None:
        _clear_active_recipe_projection(tool_ctx)
        return _recipe_validation_error_response(name, result)
    _cache_finalized_recipe_projection(tool_ctx, _finalized_projection)

    if (
        preflight_error := await _preflight_named_recipe(
            ctx, tool_ctx, name, _finalized_projection, prune_stale=True
        )
    ) is not None:
        return preflight_error

    # Snapshot the caller-supplied values ONLY — NOT _merged_overrides.
    # Storing _merged_overrides would inject stale kitchen_id/diagnostics_log_dir
    # into subsequent load_recipe merges, silently overwriting fresh infra values.
    tool_ctx.session_serve_overrides = dict(overrides) if overrides else {}
    tool_ctx.session_serve_defer_unresolved = not bool(overrides)

    result = build_open_kitchen_recipe_payload(result, version=__version__)

    _override_warnings = _check_override_keys(
        overrides,
        _finalized_projection.ingredient_names,
        set(_session_overrides.keys()),
    )
    if _override_warnings:
        result["warnings"] = _override_warnings

    try:
        warning = (
            _tk_pkg._build_hook_diagnostic_warning(
                claude_plugin_tool_namespace=(
                    tool_ctx.backend.capabilities.claude_plugin_tool_namespace
                )
            )
            if tool_ctx.backend is not None
            else None
        )
    except Exception as exc:
        _clear_active_recipe_projection(tool_ctx)
        logger.warning("open_kitchen_failure", stage="hook_diagnostic", exc_info=True)
        return _kitchen_failure_envelope(exc, stage="hook_diagnostic")
    if warning:
        result["hook_warning"] = warning.strip()

    _required_keys = frozenset({"success", "content", "valid"})
    _validation_err = _validate_result(
        result, required_keys=_required_keys, tool_name="open_kitchen"
    )
    if _validation_err is not None:
        _clear_active_recipe_projection(tool_ctx)
        logger.warning(
            "open_kitchen_fail_closed",
            tool="open_kitchen",
            stage="validate_result",
        )
        return _validation_err

    return _finalize_named_recipe_delivery(
        result,
        name=name,
        tool_ctx=tool_ctx,
        projection=_finalized_projection,
        surface="open_kitchen",
        delivery_request=delivery_request,
    )
