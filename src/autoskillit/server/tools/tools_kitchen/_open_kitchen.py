"""open_kitchen tool and the gate-enablement/transition handler.

Imports the heavily monkey-patched internal helpers via the
``autoskillit.server.tools.tools_kitchen`` facade so that tests using
``mock.patch("autoskillit.server.tools.tools_kitchen._foo")`` reach the
calling site, regardless of which submodule actually defines the
function body.
"""

from __future__ import annotations

import inspect
import json
from typing import Any, cast

from fastmcp import Context
from fastmcp.dependencies import CurrentContext
from mcp.types import ToolListChangedNotification

from autoskillit import __version__
from autoskillit.config import (
    build_config_authoritative_layer,
    build_config_default_layer,
    resolve_ingredient_defaults,
)
from autoskillit.core import (
    PIPELINE_FORBIDDEN_TOOLS,
    ProcessStaleError,
    RecipeDeliveryRequest,
    RecipeLoadError,
    detect_autoskillit_mcp_prefix,
    get_logger,
    sweep_stale_markers,
)
from autoskillit.fleet import (
    discover_campaign_state_files,
    reap_stale_dispatches_async,
)
from autoskillit.pipeline import (
    KITCHEN_EFFECT_RECIPE_SERVING,
    KitchenOpenPhase,
    advance_kitchen_phase,
    create_background_task,
    transition_abort,
    transition_ambiguous,
    transition_confirm,
    transition_degraded,
)
from autoskillit.server import mcp
from autoskillit.server._guards import _backend_supports_quota, _require_orchestrator_exact
from autoskillit.server._misc import (
    _apply_triage_gate,
    _build_hook_diagnostic_warning,
    _prime_quota_cache,
    _quota_refresh_loop,
    resolve_log_dir,
    strip_ingredients_only_keys,
)
from autoskillit.server._notify import track_response_size
from autoskillit.server._recipe_delivery import (
    document_recipe_delivery_contract,
    finalize_recipe_delivery,
    prepare_recipe_delivery_generation,
)
from autoskillit.server._recipe_execution import clear_recipe_execution
from autoskillit.server.tools._auto_overrides import _compute_effective_backend_map
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._preflight import (
    _check_dispatch_feasibility,
    filter_steps_by_post_prune,
)
from autoskillit.server.tools._serve_helpers import (
    _admit_recipe_name,
    build_backend_capabilities_map,
    build_open_kitchen_recipe_payload,
    pop_finalized_recipe_projection,
    project_orchestrator_guidance,
    render_served_response,
    response_backstop_tool_meta,
    serve_recipe,
)
from autoskillit.server.tools._types import _validate_result
from autoskillit.server.tools.tools_kitchen import (  # via facade for monkey-patch reach
    _OPEN_KITCHEN_REQUEST_CTX,
    _attach_transition_fields,
    _auto_init_pipeline_tracker,
    _bind_open_kitchen_transition,
    _build_tool_category_listing,
    _check_override_keys,
    _kitchen_failure_envelope,
    _open_kitchen_cancellation_response,
    _pipeline_tracker_auto_init_failure,
    _read_open_kitchen_request_ctx,
    _recipe_validation_error_response,
    _render_ingredients_only_response,
    _write_hook_config,
)
from autoskillit.server.tools.tools_kitchen._open_kitchen_transition import (
    _ensure_kitchen_transition,
    _transition_start,
)
from autoskillit.server.tools.tools_kitchen._tracker_authority import (
    _register_active_recipe_kitchen,
)

logger = get_logger(__name__)


async def _open_kitchen_handler(*, preserve_active_recipe: bool = False) -> str | None:
    """Set the tools-enabled flag. Extracted for testability.

    Returns ``None`` on success, or a JSON failure envelope string on error.
    """
    from autoskillit.server import _get_ctx  # circular-break

    ctx = _get_ctx()
    _ensure_kitchen_transition(ctx)
    if _transition_start(ctx, "gate_enablement"):
        ctx.gate.enable()
        transition_confirm(
            ctx,
            "gate_enablement",
            receipt="gate:enabled",
            downstream_identity=ctx.kitchen_id,
        )
    if not preserve_active_recipe and _transition_start(ctx, "active_recipe_reset"):
        ctx.active_recipe_packs = frozenset()
        ctx.active_recipe_features = frozenset()
        ctx.active_recipe_steps = {}
        ctx.active_recipe_ingredients = frozenset()
        clear_recipe_execution(ctx)
        transition_confirm(ctx, "active_recipe_reset", receipt="active_recipe:cleared")
    logger.info("open_kitchen", gate_state="open", kitchen_id=ctx.kitchen_id)
    _supports_quota = _backend_supports_quota(ctx)

    if _transition_start(ctx, "hook_configuration"):
        try:
            _write_hook_config()
        except Exception as exc:
            ctx.gate.disable()
            transition_ambiguous(ctx, "hook_configuration", exc)
            logger.warning("open_kitchen_failure", stage="write_hook_config", exc_info=True)
            return _kitchen_failure_envelope(exc, stage="write_hook_config")
        transition_confirm(ctx, "hook_configuration", receipt="hook_config:written")

    if _transition_start(ctx, "quota_cache_prime"):
        try:
            await _prime_quota_cache(supports_quota_check=_supports_quota)
        except Exception as exc:
            ctx.gate.disable()
            transition_ambiguous(ctx, "quota_cache_prime", exc)
            logger.warning("open_kitchen_failure", stage="prime_quota_cache", exc_info=True)
            return _kitchen_failure_envelope(exc, stage="prime_quota_cache")
        transition_confirm(ctx, "quota_cache_prime", receipt="quota_cache:primed")

    if _transition_start(ctx, "quota_task_start"):
        if ctx.quota_refresh_task is not None:
            ctx.quota_refresh_task.cancel()
        try:
            ctx.quota_refresh_task = create_background_task(
                _quota_refresh_loop(
                    ctx.config.quota_guard,
                    supports_quota_check=_supports_quota,
                ),
                label="quota_refresh_loop",
            )
        except Exception as exc:
            ctx.gate.disable()
            transition_ambiguous(ctx, "quota_task_start", exc)
            logger.warning("open_kitchen_failure", stage="start_quota_refresh", exc_info=True)
            return _kitchen_failure_envelope(exc, stage="start_quota_refresh")
        transition_confirm(
            ctx,
            "quota_task_start",
            receipt="quota_task:owned",
            downstream_identity=str(id(ctx.quota_refresh_task)),
        )

    if _transition_start(ctx, "registry_update"):
        try:
            from autoskillit.server.tools.tools_kitchen._tracker_authority import (
                _retain_kitchen_tracker_authority,
            )

            _retain_kitchen_tracker_authority(ctx)
            _register_active_recipe_kitchen(ctx)
        except Exception as exc:
            transition_degraded(ctx, "registry_update", exc)
            logger.warning("open_kitchen_registry_failed", exc_info=True)
        else:
            transition_confirm(
                ctx,
                "registry_update",
                receipt="registry:kitchen_registered",
                downstream_identity=ctx.kitchen_id,
            )

    if _transition_start(ctx, "tracker_prune"):
        try:
            from autoskillit.server.tools.tools_kitchen._tracker_authority import (
                prune_stale_kitchen_state,
            )

            prune_stale_kitchen_state(ctx.project_dir, ctx.kitchen_id)
        except Exception as exc:
            transition_degraded(ctx, "tracker_prune", exc)
            logger.warning("open_kitchen_prune_trackers_failed", exc_info=True)
        else:
            transition_confirm(ctx, "tracker_prune", receipt="trackers:pruned")

    if _transition_start(ctx, "marker_sweep"):
        try:
            sweep_stale_markers()
        except Exception as exc:
            transition_degraded(ctx, "marker_sweep", exc)
            logger.warning("open_kitchen_sweep_markers_failed", exc_info=True)
        else:
            transition_confirm(ctx, "marker_sweep", receipt="markers:swept")

    if _transition_start(ctx, "stale_dispatch_reap"):
        try:
            _campaign_state_paths = discover_campaign_state_files(ctx.project_dir)
            if _campaign_state_paths:
                await reap_stale_dispatches_async(
                    _campaign_state_paths,
                    min_reap_age_seconds=60.0,
                    heartbeat_grace_seconds=90.0,
                )
        except Exception as exc:
            transition_degraded(ctx, "stale_dispatch_reap", exc)
            logger.warning("open_kitchen_reap_failed", exc_info=True)
        else:
            transition_confirm(ctx, "stale_dispatch_reap", receipt="dispatches:reaped")

    ctx.gate_infrastructure_ready = True
    return None


async def _redisable_subsets(
    ctx: Context,
    disabled: list[str],
    features: dict[str, bool] | None = None,
    *,
    experimental_enabled: bool = False,
) -> None:
    """Re-disable subset-tagged and feature-disabled tools after enabling kitchen.

    Pass 1 (existing): Re-disable config-disabled subset tags so dual-tagged tools
    (e.g. kitchen+github) that are server-disabled are not accidentally revealed.

    Pass 2: Suppress tool tags for disabled features via `_collect_disabled_feature_tags`.
    Shared tools with kitchen-core retain visibility via the kitchen-core tag
    (FastMCP union model).

    ``features`` defaults to ``None`` (treated as ``{}``, i.e. all features use
    ``FeatureDef.default_enabled``). Pass ``config.features`` from the call site.
    """

    async def _disable_tag(tag: str) -> None:
        result = ctx.disable_components(tags={tag})
        if inspect.isawaitable(result):
            await result

    # Pass 1: subset re-disable (existing)
    for subset in disabled:
        await _disable_tag(subset)

    # Pass 2: feature gate — suppress tool tags for disabled features
    _features = features or {}
    for tag in _collect_disabled_feature_tags(
        _features, experimental_enabled=experimental_enabled
    ):
        await _disable_tag(tag)


def _collect_disabled_feature_tags(
    features: dict[str, bool] | None = None, *, experimental_enabled: bool = False
) -> frozenset[str]:
    from autoskillit.core import _collect_disabled_feature_tags as _real_collect

    return _real_collect(features or {}, experimental_enabled=experimental_enabled)


@mcp.tool(
    tags={"autoskillit"},
    annotations={"readOnlyHint": False},
    meta=response_backstop_tool_meta("open_kitchen", always_load=True),
)
@document_recipe_delivery_contract
@_bind_open_kitchen_transition
@_cancellation_shield(
    state_factory=_read_open_kitchen_request_ctx,
    state_context_var=_OPEN_KITCHEN_REQUEST_CTX,
    response_factory=_open_kitchen_cancellation_response,
)
@track_response_size("open_kitchen")
async def open_kitchen(
    name: str | None = None,
    overrides: dict[str, str] | None = None,
    ingredients_only: bool = False,
    delivery_request: RecipeDeliveryRequest | None = None,
    ctx: Context = CurrentContext(),
) -> str:
    """Open the AutoSkillit kitchen for service.

    A no-argument call made solely to gain access is unnecessary when authoritative
    session guidance says the kitchen was pre-revealed. Valid uses remain
    human-requested activation when access is not active, human-requested promotion
    including from a pre-revealed session, named recipe loading with ``name=...``, and
    restoration after close_kitchen.

    When ``name`` is provided, the kitchen is opened AND the named recipe is
    loaded in a single call, reducing terminal noise from two tool calls to one.

    ``$<name>`` or ``/<name>`` denotes an in-session skill invocation. Do not pass
    a skill name to ``open_kitchen``, ``load_recipe``, ``migrate_recipe``, or
    ``recipe://``; those surfaces accept recipe identities only.
    A name defined as both a recipe and a skill is rejected until one artifact
    is renamed.

    Args:
        name: Optional recipe name to load immediately after opening.
        overrides: Optional dict of ingredient name → value to override recipe defaults.
            Use to activate hidden features (e.g., ``{"sprint_mode": "true"}``). Ingredients
            with ``authority: config`` (base_branch, local_review_rounds,
            adversarial_review_level) cannot be set via overrides — they resolve from
            server config and caller values are ignored with a warning.
            Config-default ingredients (pipeline_health) use config as the default
            but an explicit override wins.
        ingredients_only: When True and name is provided, return only the ingredient
            schema (ingredients_table, validity, suggestions) without the full recipe
            content, orchestration rules, or sous-chef discipline. Use for dispatch
            workflows where the caller needs ingredient discovery but not pipeline
            execution context.

    Never raises.
    """
    try:
        # Headless guard — wrap denial in envelope shape
        if (h := _require_orchestrator_exact("open_kitchen")) is not None:
            parsed_h = json.loads(h)
            return json.dumps(
                {
                    "success": False,
                    "kitchen": "failed",
                    "user_visible_message": parsed_h.get(
                        "result",
                        "open_kitchen cannot be called from headless sessions.",
                    ),
                    "error": "HeadlessDenied",
                    "stage": "headless_guard",
                }
            )

        from autoskillit.server import _get_ctx  # circular-break

        _ctx_pre = _get_ctx()
        _admitted_recipe_info = None
        if name is not None:
            if _ctx_pre.recipes is None or _ctx_pre.skill_resolver is None:
                missing_service = (
                    "recipe repository" if _ctx_pre.recipes is None else "skill resolver"
                )
                return _kitchen_failure_envelope(
                    RuntimeError(f"{missing_service} is not configured"),
                    stage="recipe_context",
                    user_hint=(
                        "open_kitchen cannot load a recipe because the server is not "
                        "initialized. Run 'autoskillit doctor' to diagnose."
                    ),
                )
            try:
                _admitted_recipe_info = _admit_recipe_name(_ctx_pre, name)
            except RecipeLoadError as exc:
                return _kitchen_failure_envelope(
                    exc,
                    stage="recipe_namespace",
                    user_hint=str(exc),
                )

        disabled_subsets = _ctx_pre.config.subsets.disabled
        _skip_handler = _ctx_pre.gate_infrastructure_ready
        tool_ctx = _get_ctx()

        if not _skip_handler:
            handler_err = await _open_kitchen_handler(
                preserve_active_recipe=ingredients_only and _ctx_pre.gate.enabled,
            )
            if handler_err is not None:
                return handler_err
        else:
            _ctx_post = _get_ctx()
            if _ctx_post.quota_refresh_task is None:
                _supports_quota_post = _backend_supports_quota(_ctx_post)
                try:
                    _ctx_post.quota_refresh_task = create_background_task(
                        _quota_refresh_loop(
                            _ctx_post.config.quota_guard,
                            supports_quota_check=_supports_quota_post,
                        ),
                        label="quota_refresh_loop",
                    )
                except Exception:
                    logger.warning(
                        "open_kitchen_quota_refresh_deferred_start_failed", exc_info=True
                    )

        if not _skip_handler:
            # Scope-placement invariant (REQ-#4399): this branch is gated on
            # `gate_infrastructure_ready == False` — i.e., tags can only be
            # disabled by close_kitchen(), which always calls
            # _close_kitchen_handler(), and that handler unconditionally sets
            # `gate_infrastructure_ready = False`. When _skip_handler=True
            # (gate_infrastructure_ready was already True), tags are already
            # correctly enabled — either from _pre_reveal_kitchen() at boot or
            # from a prior open_kitchen() that ran the enable block. Therefore
            # _skip_handler=True is structurally unreachable after a
            # close_kitchen call; any future change to close_kitchen's
            # gate_infrastructure_ready transition must preserve this
            # invariant or it will silently break the notification asymmetry
            # fixed in #4399.
            _kctx_pre = _get_ctx()
            _use_global_enable = (
                _kctx_pre.backend is not None
                and not _kctx_pre.backend.capabilities.supports_tool_list_changed
            )
            if _use_global_enable:
                # Issue #4399: when the backend can't process tool/list_changed
                # notifications, ctx.enable_components() is skipped.
                # close_kitchen() appends global mcp.disable() for these tags,
                # so without a refresh here, the tags would never re-enable.
                # Append global enables to override the prior disables via
                # FastMCP's last-match-wins, then send an explicit
                # ToolListChangedNotification so any connected Client refreshes
                # its stale tool cache. (close_kitchen's notification only
                # refreshes after disable; without an explicit re-enable
                # notification, the client keeps serving the post-close list.)
                if _transition_start(tool_ctx, "client_visibility"):
                    mcp.enable(tags={"kitchen"})
                    mcp.enable(tags={"plan-review"})
                    transition_confirm(
                        tool_ctx,
                        "client_visibility",
                        receipt="visibility:global_enabled",
                    )
                    logger.debug("open_kitchen_global_enables", reason="use_global_enable")
                if _transition_start(tool_ctx, "visibility_notification"):
                    try:
                        await ctx.send_notification(ToolListChangedNotification())
                    except Exception as exc:
                        transition_degraded(tool_ctx, "visibility_notification", exc)
                        logger.warning(
                            "open_kitchen_notify_failed",
                            stage="send_notification",
                            exc_info=True,
                        )
                    else:
                        transition_confirm(
                            tool_ctx,
                            "visibility_notification",
                            receipt="visibility:list_changed_sent",
                        )
            else:
                if _transition_start(tool_ctx, "client_visibility"):
                    try:
                        await ctx.enable_components(tags={"kitchen"})
                    except Exception as exc:
                        transition_ambiguous(tool_ctx, "client_visibility", exc)
                        logger.warning(
                            "open_kitchen_failure", stage="enable_components", exc_info=True
                        )
                        tool_ctx.gate_infrastructure_ready = False
                        return _kitchen_failure_envelope(exc, stage="enable_components")
                    transition_confirm(
                        tool_ctx,
                        "client_visibility",
                        receipt="visibility:client_enabled",
                    )

            if _transition_start(tool_ctx, "subset_visibility"):
                try:
                    _kctx = _get_ctx()
                    await _redisable_subsets(
                        ctx,
                        disabled_subsets,
                        _kctx.config.features,
                        experimental_enabled=_kctx.config.experimental_enabled,
                    )
                except Exception as exc:
                    transition_ambiguous(tool_ctx, "subset_visibility", exc)
                    logger.warning(
                        "open_kitchen_failure", stage="redisable_subsets", exc_info=True
                    )
                    tool_ctx.gate_infrastructure_ready = False
                    return _kitchen_failure_envelope(exc, stage="redisable_subsets")
                transition_confirm(
                    tool_ctx,
                    "subset_visibility",
                    receipt="visibility:subsets_reconciled",
                )
            with tool_ctx.kitchen_transition_lock:
                if tool_ctx.kitchen_open_state.phase is KitchenOpenPhase.REQUEST_BOUND:
                    tool_ctx.kitchen_open_state = advance_kitchen_phase(
                        tool_ctx.kitchen_open_state,
                        KitchenOpenPhase.VISIBILITY_READY,
                    )

        _is_deferred_recall = (
            name is not None
            and _ctx_pre.gate.enabled
            and _ctx_pre.recipe_name == name
            and _ctx_pre.recipe_name != ""
        )

        _forbidden_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)
        _ctx = _get_ctx()
        _categories = _build_tool_category_listing(
            _ctx.config.features, experimental_enabled=_ctx.config.experimental_enabled
        )

        if name is not None:
            tool_ctx = _get_ctx()
            if not ingredients_only:
                clear_recipe_execution(tool_ctx)
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
            _defaults = resolve_ingredient_defaults(tool_ctx.project_dir)
            assert _admitted_recipe_info is not None
            _recipe_info = _admitted_recipe_info
            _raw_recipe = tool_ctx.recipes.load(_recipe_info.path)
            _session_overrides: dict[str, str] = {
                "kitchen_id": tool_ctx.kitchen_id,
                "diagnostics_log_dir": str(resolve_log_dir(tool_ctx.config.linux_tracing.log_dir)),
            }
            _config_layer = build_config_authoritative_layer(_defaults)
            _config_default = build_config_default_layer(_defaults)
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
            if _is_deferred_recall:
                try:
                    _transition_start(tool_ctx, KITCHEN_EFFECT_RECIPE_SERVING)
                    result = serve_recipe(
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
                    _deferred_finalized_projection = (
                        pop_finalized_recipe_projection(result)
                        if result.get("valid", False)
                        else None
                    )
                except ProcessStaleError as exc:
                    logger.warning("open_kitchen_failure", stage="process_stale", exc_info=True)
                    return _kitchen_failure_envelope(exc, stage="process_stale")
                except Exception as exc:
                    logger.warning(
                        "open_kitchen_failure", stage="load_and_validate", exc_info=True
                    )
                    return _kitchen_failure_envelope(exc, stage="load_and_validate")
                if ingredients_only:
                    if not result.get("valid", False):
                        transition_abort(tool_ctx, KITCHEN_EFFECT_RECIPE_SERVING)
                    return _render_ingredients_only_response(
                        result,
                        declared_ingredients=(
                            frozenset(_raw_recipe.ingredients) if _raw_recipe is not None else None
                        ),
                        overrides=overrides,
                        session_keys=set(_session_overrides),
                        config_layer=_config_layer,
                    )
                tool_ctx.active_recipe_packs = frozenset(result.get("requires_packs", []))
                tool_ctx.active_recipe_features = frozenset(result.get("requires_features", []))
                tool_ctx.recipe_content_hash = result.get("content_hash", "")
                tool_ctx.recipe_composite_hash = result.get("composite_hash", "")
                tool_ctx.recipe_version = result.get("recipe_version") or ""
                recipe_info = _recipe_info
                _deferred_recipe_obj = None
                try:
                    recipe_obj = tool_ctx.recipes.load(recipe_info.path)
                    _deferred_recipe_obj = recipe_obj
                    tool_ctx.active_recipe_steps = filter_steps_by_post_prune(
                        recipe_obj.steps, result.get("post_prune_step_names", [])
                    )
                    tool_ctx.active_recipe_ingredients = frozenset(recipe_obj.ingredients.keys())
                except Exception:
                    logger.warning("open_kitchen_recipe_steps_cache_failed", exc_info=True)
                    tool_ctx.active_recipe_steps = None
                    tool_ctx.active_recipe_ingredients = None
                # Default to False for missing 'valid' so a absent key is treated as invalid
                if not result.get("valid", False) or not result.get("content", ""):
                    transition_abort(tool_ctx, KITCHEN_EFFECT_RECIPE_SERVING)
                    tool_ctx.gate.disable()
                    tool_ctx.gate_infrastructure_ready = False
                    return _recipe_validation_error_response(name, result)
                # Dispatch-feasibility preflight: verify the backend can enforce
                # all fix-required hooks for the recipe's run_skill steps.
                if tool_ctx.active_recipe_steps is not None:
                    _tracker_error = _auto_init_pipeline_tracker(tool_ctx)
                    if _tracker_error is not None:
                        return _pipeline_tracker_auto_init_failure(tool_ctx, _tracker_error)
                    _preflight_err = _check_dispatch_feasibility(
                        post_prune_step_names=result.get("post_prune_step_names", []),
                        active_recipe_steps=tool_ctx.active_recipe_steps,
                        backend=tool_ctx.backend,
                        config_providers=tool_ctx.config.providers,
                        recipe_name=name,
                        config_backend=tool_ctx.config.agent_backend,
                        skill_resolver=tool_ctx.skill_resolver,
                        project_root=tool_ctx.project_dir,
                        temp_dir=tool_ctx.temp_dir,
                    )
                    if _preflight_err is not None:
                        transition_abort(tool_ctx, KITCHEN_EFFECT_RECIPE_SERVING)
                        tool_ctx.gate.disable()
                        tool_ctx.gate_infrastructure_ready = False
                        await ctx.disable_components(tags={"kitchen"})
                        return _preflight_err
                result = build_open_kitchen_recipe_payload(result, version=__version__)
                try:
                    result = await _apply_triage_gate(result, name, recipe_info=recipe_info)
                except Exception as exc:
                    logger.warning(
                        "open_kitchen_failure", stage="apply_triage_gate", exc_info=True
                    )
                    return _kitchen_failure_envelope(exc, stage="apply_triage_gate")
                if _deferred_recipe_obj is not None:
                    _override_warnings = _check_override_keys(
                        overrides,
                        frozenset(_deferred_recipe_obj.ingredients.keys()),
                        set(_session_overrides.keys()),
                        _config_layer,
                    )
                    if _override_warnings:
                        result["warnings"] = _override_warnings
                if ingredients_only:
                    result = strip_ingredients_only_keys(result)
                # When caller provides explicit overrides, update the snapshot so
                # subsequent load_recipe/get_recipe calls see the new overrides.
                # When overrides=None (replay previous context), leave the existing
                # snapshot intact — the caller's intent is continuity, not reset.
                if overrides is not None:
                    tool_ctx.session_serve_overrides = dict(overrides)
                    tool_ctx.session_serve_defer_unresolved = not bool(overrides)
                if not ingredients_only:
                    if _deferred_finalized_projection is None:
                        return _recipe_validation_error_response(name, result)
                    _prepared_generation = prepare_recipe_delivery_generation(
                        result,
                        recipe_name=name,
                        tool_ctx=tool_ctx,
                        finalized_projection=_deferred_finalized_projection,
                    )
                    _attach_transition_fields(result, tool_ctx, committed=True)
                    return cast(
                        str,
                        finalize_recipe_delivery(
                            result,
                            surface="open_kitchen_deferred_recall",
                            recipe_name=name,
                            tool_ctx=tool_ctx,
                            finalized_projection=_deferred_finalized_projection,
                            flow_generation=_prepared_generation.flow_generation,
                            canonical_artifact_payload=(
                                _prepared_generation.canonical_artifact_payload
                            ),
                            execution_snapshot=(_prepared_generation.execution_snapshot),
                            normalized_compile_key=(_prepared_generation.normalized_compile_key),
                            delivery_request=delivery_request,
                        ),
                    )
                return render_served_response(result)
            try:
                _transition_start(tool_ctx, KITCHEN_EFFECT_RECIPE_SERVING)
                result = serve_recipe(
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
                _normal_finalized_projection = (
                    pop_finalized_recipe_projection(result) if result.get("valid", False) else None
                )
            except ProcessStaleError as exc:
                logger.warning("open_kitchen_failure", stage="process_stale", exc_info=True)
                return _kitchen_failure_envelope(exc, stage="process_stale")
            except Exception as exc:
                logger.warning("open_kitchen_failure", stage="load_and_validate", exc_info=True)
                return _kitchen_failure_envelope(exc, stage="load_and_validate")
            if ingredients_only:
                if not result.get("valid", False):
                    transition_abort(tool_ctx, KITCHEN_EFFECT_RECIPE_SERVING)
                return _render_ingredients_only_response(
                    result,
                    declared_ingredients=(
                        frozenset(_raw_recipe.ingredients) if _raw_recipe is not None else None
                    ),
                    overrides=overrides,
                    session_keys=set(_session_overrides),
                    config_layer=_config_layer,
                )

            tool_ctx.active_recipe_packs = frozenset(result.get("requires_packs", []))
            tool_ctx.active_recipe_features = frozenset(result.get("requires_features", []))
            tool_ctx.recipe_name = name
            tool_ctx.recipe_content_hash = result.get("content_hash", "")
            tool_ctx.recipe_composite_hash = result.get("composite_hash", "")
            tool_ctx.recipe_version = result.get("recipe_version") or ""

            try:
                from autoskillit.server.tools.tools_kitchen import (
                    _update_hook_config_with_git_ops_policy,
                    _update_hook_config_with_recipe,
                )

                _update_hook_config_with_recipe()
                _update_hook_config_with_git_ops_policy()
            except Exception:
                logger.warning("open_kitchen_failure", stage="update_hook_config", exc_info=True)

            composite = result.get("composite_hash", "")
            from autoskillit.server._state import _check_rerun  # circular-break

            rerun_suggestion = _check_rerun(tool_ctx.config.linux_tracing.log_dir, composite)
            if rerun_suggestion:
                result.setdefault("suggestions", []).append(rerun_suggestion)

            recipe_info = _recipe_info

            _normal_recipe_obj = None
            try:
                recipe_obj = tool_ctx.recipes.load(recipe_info.path)
                _normal_recipe_obj = recipe_obj
                tool_ctx.active_recipe_steps = filter_steps_by_post_prune(
                    recipe_obj.steps, result.get("post_prune_step_names", [])
                )
                tool_ctx.active_recipe_ingredients = frozenset(recipe_obj.ingredients.keys())
            except Exception:
                logger.warning("open_kitchen_recipe_steps_cache_failed", exc_info=True)
                tool_ctx.active_recipe_steps = None
                tool_ctx.active_recipe_ingredients = None

            try:
                result = await _apply_triage_gate(result, name, recipe_info=recipe_info)
            except Exception as exc:
                logger.warning("open_kitchen_failure", stage="apply_triage_gate", exc_info=True)
                return _kitchen_failure_envelope(exc, stage="apply_triage_gate")

            if not result.get("valid", False) or not result.get("content", ""):
                transition_abort(tool_ctx, KITCHEN_EFFECT_RECIPE_SERVING)
                tool_ctx.gate.disable()
                tool_ctx.gate_infrastructure_ready = False
                return _recipe_validation_error_response(name, result)

            # Dispatch-feasibility preflight: verify the backend can enforce
            # all fix-required hooks for the recipe's run_skill steps.
            if tool_ctx.active_recipe_steps is not None:
                try:
                    from autoskillit.server.tools.tools_kitchen import (
                        prune_stale_kitchen_state,
                    )

                    prune_stale_kitchen_state(tool_ctx.project_dir, tool_ctx.kitchen_id)
                except Exception:
                    logger.warning("open_kitchen_deferred_prune_failed", exc_info=True)
                _tracker_error = _auto_init_pipeline_tracker(tool_ctx)
                if _tracker_error is not None:
                    return _pipeline_tracker_auto_init_failure(tool_ctx, _tracker_error)
                _preflight_err = _check_dispatch_feasibility(
                    post_prune_step_names=result.get("post_prune_step_names", []),
                    active_recipe_steps=tool_ctx.active_recipe_steps,
                    backend=tool_ctx.backend,
                    config_providers=tool_ctx.config.providers,
                    recipe_name=name,
                    config_backend=tool_ctx.config.agent_backend,
                    skill_resolver=tool_ctx.skill_resolver,
                    project_root=tool_ctx.project_dir,
                    temp_dir=tool_ctx.temp_dir,
                )
                if _preflight_err is not None:
                    transition_abort(tool_ctx, KITCHEN_EFFECT_RECIPE_SERVING)
                    tool_ctx.gate.disable()
                    tool_ctx.gate_infrastructure_ready = False
                    await ctx.disable_components(tags={"kitchen"})
                    return _preflight_err

            # Snapshot the caller-supplied values ONLY — NOT _merged_overrides.
            # Storing _merged_overrides would inject stale kitchen_id/diagnostics_log_dir
            # into subsequent load_recipe merges, silently overwriting fresh infra values.
            tool_ctx.session_serve_overrides = dict(overrides) if overrides else {}
            tool_ctx.session_serve_defer_unresolved = not bool(overrides)

            result = build_open_kitchen_recipe_payload(result, version=__version__)

            if ingredients_only:
                result = strip_ingredients_only_keys(result)

            if _normal_recipe_obj is not None:
                _override_warnings = _check_override_keys(
                    overrides,
                    frozenset(_normal_recipe_obj.ingredients.keys()),
                    set(_session_overrides.keys()),
                    _config_layer,
                )
                if _override_warnings:
                    result["warnings"] = _override_warnings

            try:
                warning = (
                    _build_hook_diagnostic_warning(
                        detect_autoskillit_mcp_prefix(tool_ctx.backend.capabilities)
                    )
                    if tool_ctx.backend is not None
                    else None
                )
            except Exception as exc:
                logger.warning("open_kitchen_failure", stage="hook_diagnostic", exc_info=True)
                return _kitchen_failure_envelope(exc, stage="hook_diagnostic")
            if warning:
                result["hook_warning"] = warning.strip()

            _required_keys = frozenset({"success", "content", "valid"})
            if ingredients_only:
                _required_keys = _required_keys - {"content"}
            _validation_err = _validate_result(
                result, required_keys=_required_keys, tool_name="open_kitchen"
            )
            if _validation_err is not None:
                logger.warning(
                    "open_kitchen_fail_closed",
                    tool="open_kitchen",
                    stage="validate_result",
                )
                return _validation_err

            if not ingredients_only:
                if _normal_finalized_projection is None:
                    return _recipe_validation_error_response(name, result)
                _prepared_generation = prepare_recipe_delivery_generation(
                    result,
                    recipe_name=name,
                    tool_ctx=tool_ctx,
                    finalized_projection=_normal_finalized_projection,
                )
                _attach_transition_fields(result, tool_ctx, committed=True)
                return cast(
                    str,
                    finalize_recipe_delivery(
                        result,
                        surface="open_kitchen",
                        recipe_name=name,
                        tool_ctx=tool_ctx,
                        finalized_projection=_normal_finalized_projection,
                        flow_generation=_prepared_generation.flow_generation,
                        canonical_artifact_payload=(
                            _prepared_generation.canonical_artifact_payload
                        ),
                        execution_snapshot=_prepared_generation.execution_snapshot,
                        normalized_compile_key=(_prepared_generation.normalized_compile_key),
                        delivery_request=delivery_request,
                    ),
                )

            return render_served_response(result)

        _transition_start(tool_ctx, "anonymous_response")
        text = (
            f"Kitchen is open. AutoSkillit {__version__}. Tools are ready for service.\n\n"
            f"Available Tools by Category:\n{_categories}\n\n"
            "IMPORTANT — Orchestrator Discipline:\n"
            f"NEVER use native Claude Code tools ({_forbidden_list}) "
            "in this session. All code reading, searching, editing, and "
            "investigation MUST be delegated through run_skill, which launches "
            "headless sessions with full tool access. Do NOT use native tools to "
            "investigate failures — route to on_failure "
            "and let the downstream skill handle diagnosis."
        )

        # Anonymous opens receive the projected orchestrator discipline. Named opens
        # returned above and preserve their attested recipe-delivery bytes unchanged.
        try:
            text += project_orchestrator_guidance(_ctx)
        except Exception as exc:
            logger.warning("open_kitchen_failure", stage="project_sous_chef", exc_info=True)
            return _kitchen_failure_envelope(exc, stage="project_sous_chef")

        # Check if the project needs an upgrade
        scripts_dir = _ctx.project_dir / ".autoskillit" / "scripts"
        recipes_dir = _ctx.project_dir / ".autoskillit" / "recipes"
        if scripts_dir.exists() and not recipes_dir.exists():
            text += (
                "\n\n⚠️ UPGRADE NEEDED: This project has not been migrated"
                " to the new recipe format.\n"
                "`.autoskillit/scripts/` still exists."
                " Run `autoskillit upgrade` in this directory\n"
                "to migrate automatically, or ask me to do it for you."
            )

        try:
            warning = (
                _build_hook_diagnostic_warning(
                    detect_autoskillit_mcp_prefix(tool_ctx.backend.capabilities)
                )
                if tool_ctx.backend is not None
                else None
            )
        except Exception as exc:
            logger.warning("open_kitchen_failure", stage="hook_diagnostic", exc_info=True)
            return _kitchen_failure_envelope(exc, stage="hook_diagnostic")
        if warning:
            text += warning

        anonymous_result: dict[str, Any] = {
            "success": True,
            "kitchen": "open",
            "content": text,
            "ingredients_table": None,
            "version": __version__,
        }
        _attach_transition_fields(anonymous_result, tool_ctx, committed=True)
        return render_served_response(anonymous_result)
    except Exception as exc:
        logger.error("open_kitchen unhandled exception", exc_info=True)
        return _kitchen_failure_envelope(exc, stage="unhandled")
