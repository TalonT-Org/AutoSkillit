"""open_kitchen tool: request-shape guards, visibility management, and
dispatch to the gate-enablement handler and named-recipe serve pipeline.

Decomposed from the flat ``_open_kitchen.py`` module (over the REQ-CNST-010
750-line cap) into a shard package: ``_gate.py`` owns the gate-enablement
handler, ``_visibility.py`` owns subset/feature tool-visibility
reconciliation, and ``_recipe_serve.py`` owns named-recipe serving (shared
setup plus the deferred-recall/normal-serve pair). This module keeps the
orchestrator body and the anonymous-open response.

Cross-submodule helpers are imported directly from their submodules
(``._open_kitchen_transition``, ``._open_kitchen_errors``, ``._get_recipe``)
to avoid a circular-import hazard through the package facade.
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp import Context
from fastmcp.dependencies import CurrentContext
from mcp.types import ToolListChangedNotification

from autoskillit import __version__
from autoskillit.config import SERVER_AUTHORITATIVE_INGREDIENTS
from autoskillit.core import (
    PIPELINE_FORBIDDEN_TOOLS,
    RecipeDeliveryRequest,
    RecipeLoadError,
    detect_autoskillit_mcp_prefix,
    get_logger,
)
from autoskillit.core import (
    session_type as _resolve_session_type,
)
from autoskillit.pipeline import (
    KitchenOpenPhase,
    advance_kitchen_phase,
    exploration_auto_provision_eligible,
    transition_ambiguous,
    transition_confirm,
    transition_degraded,
)
from autoskillit.server import mcp
from autoskillit.server._guards import _backend_supports_quota
from autoskillit.server._notify import track_response_size
from autoskillit.server.recipe._recipe_delivery import document_recipe_delivery_contract
from autoskillit.server.tools import tools_kitchen as _tk_pkg
from autoskillit.server.tools._authority_feedback import build_authority_rejection_envelope
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._serve_helpers import (
    _admit_recipe_name,
    render_served_response,
    response_backstop_tool_meta,
)
from autoskillit.server.tools.tools_kitchen._get_recipe import _build_tool_category_listing
from autoskillit.server.tools.tools_kitchen._open_kitchen._gate import (  # noqa: F401  (facade re-export)
    _open_kitchen_handler,
)
from autoskillit.server.tools.tools_kitchen._open_kitchen._recipe_serve import (
    _cache_finalized_recipe_projection,  # noqa: F401  (facade re-export)
    _clear_active_recipe_projection,  # noqa: F401  (facade re-export)
    _serve_named_recipe,
)
from autoskillit.server.tools.tools_kitchen._open_kitchen._visibility import (  # noqa: F401  (facade re-export)
    _redisable_subsets,
)
from autoskillit.server.tools.tools_kitchen._open_kitchen_errors import _kitchen_failure_envelope
from autoskillit.server.tools.tools_kitchen._open_kitchen_transition import (
    _OPEN_KITCHEN_REQUEST_CTX,
    _attach_transition_fields,
    _bind_open_kitchen_transition,
    _open_kitchen_cancellation_response,
    _read_open_kitchen_request_ctx,
    _transition_start,
)

logger = get_logger(__name__)

__all__ = [
    "open_kitchen",
    "_open_kitchen_handler",
    "_redisable_subsets",
    "_cache_finalized_recipe_projection",
    "_clear_active_recipe_projection",
]


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
            server config and caller values are rejected with a structured error envelope.
            Typed ingredients are validated for value coercion; mismatched values are
            rejected with a structured error envelope. Config-default ingredients
            (pipeline_health) use config as the default but an explicit override wins.
        ingredients_only: When True and name is provided, return only the ingredient
            schema (ingredients_table, validity, suggestions) without the full recipe
            content, orchestration rules, or sous-chef discipline. Use for dispatch
            workflows where the caller needs ingredient discovery but not pipeline execution.

    Never raises.
    """
    try:
        if overrides:
            if authority_overlap := set(overrides.keys()) & SERVER_AUTHORITATIVE_INGREDIENTS:
                return json.dumps(build_authority_rejection_envelope(authority_overlap))

        # Headless guard — wrap denial in envelope shape
        if (h := _tk_pkg._require_orchestrator_exact("open_kitchen")) is not None:
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
            handler_err = await _tk_pkg._open_kitchen_handler(
                preserve_active_recipe=ingredients_only and _ctx_pre.gate.enabled,
            )
            if handler_err is not None:
                return handler_err
        else:
            _ctx_post = _get_ctx()
            if _ctx_post.quota_refresh_task is None:
                _supports_quota_post = _backend_supports_quota(_ctx_post)
                try:
                    _ctx_post.quota_refresh_task = _tk_pkg.create_background_task(
                        _tk_pkg._quota_refresh_loop(
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
            # #4684 Fix D: auto-provision the exploration tag alongside kitchen/
            # plan-review when opted in and the session type is eligible to bind
            # exploration authority. Visibility-only — the per-call HMAC capability
            # lease minted by enable_exploration remains the authorization boundary
            # regardless of tag visibility.
            _auto_provision_exploration = exploration_auto_provision_eligible(
                auto_provision=_kctx_pre.config.agent_backend.auto_provision_exploration,
                session_type=_resolve_session_type(),
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
                    _tk_pkg.mcp.enable(tags={"kitchen"})
                    _tk_pkg.mcp.enable(tags={"plan-review"})
                    if _auto_provision_exploration:
                        _tk_pkg.mcp.enable(tags={"exploration"})
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
                        if _auto_provision_exploration:
                            await ctx.enable_components(tags={"exploration"})
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
                    await _tk_pkg._redisable_subsets(
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
            return await _serve_named_recipe(
                ctx,
                name,
                overrides,
                ingredients_only,
                delivery_request,
                _admitted_recipe_info,
                _is_deferred_recall,
            )

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
            text += _tk_pkg.project_orchestrator_guidance(_ctx)
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
                _tk_pkg._build_hook_diagnostic_warning(
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
