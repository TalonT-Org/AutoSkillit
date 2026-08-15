"""MCP tool handlers and resource: open_kitchen, close_kitchen, recipe:// resource."""

from __future__ import annotations

import difflib
import functools
import inspect
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, cast

if TYPE_CHECKING:
    from autoskillit.config.settings import OutputBudgetConfig, QuotaGuardConfig

from fastmcp import Context
from fastmcp.dependencies import CurrentContext
from mcp.types import ToolListChangedNotification

from autoskillit import __version__
from autoskillit.config import (
    SERVER_AUTHORITATIVE_INGREDIENTS,
    build_config_authoritative_layer,
    build_config_default_layer,
    iter_display_categories,
    resolve_ingredient_defaults,
)
from autoskillit.core import (
    DISPATCH_ID_ENV_VAR,
    PIPELINE_FORBIDDEN_TOOLS,
    ArtifactLease,
    KitchenProcessIdentity,
    ProcessStaleError,
    RecipeDeliveryRequest,
    RecipeLoadError,
    TrackerAuthorityTarget,
    TrackerParticipantKey,
    _collect_disabled_feature_tags,
    atomic_write,
    detect_autoskillit_mcp_prefix,
    fast_dumps,
    find_latest_session_id,
    get_logger,
    get_state_dir,
    initialize_kitchen_tracker,
    is_marker_fresh,
    pipeline_tracker_directory,
    read_marker,
    register_active_kitchen,
    release_tracker_lease,
    resolve_kitchen_id,
    retain_tracker_lease,
    sweep_stale_markers,
    try_retire_tracker,
    unregister_active_kitchen,
)
from autoskillit.fleet import (
    FleetSemaphore,
    discover_campaign_state_files,
    reap_stale_dispatches_async,
)
from autoskillit.pipeline import (
    KITCHEN_EFFECT_RECIPE_SERVING,
    KITCHEN_EFFECT_RESPONSE_ENFORCEMENT,
    KitchenEffectPhase,
    KitchenIntentConflict,
    KitchenOpenPhase,
    KitchenRetryDisposition,
    ToolContext,
    advance_kitchen_phase,
    bind_kitchen_intent,
    canonical_kitchen_intent_fingerprint,
    claim_kitchen_request,
    closed_kitchen_open_state,
    commit_kitchen_response,
    confirm_kitchen_effect,
    create_background_task,
    get_kitchen_process_identity,
    kitchen_state_payload,
    mark_kitchen_effect_ambiguous,
    new_kitchen_open_state,
    release_kitchen_request,
    start_kitchen_effect,
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
    _hook_config_path,
    _prime_quota_cache,
    _quota_refresh_loop,
    resolve_log_dir,
    strip_ingredients_only_keys,
)
from autoskillit.server._notify import track_response_size
from autoskillit.server._recipe_delivery import (
    document_recipe_delivery_contract,
    enforce_recipe_resource_response,
    finalize_recipe_delivery,
    prepare_recipe_delivery_generation,
    retire_recipe_artifacts,
)
from autoskillit.server._recipe_execution import clear_recipe_execution
from autoskillit.server._recipe_generation import (
    retire_kitchen as retire_recipe_generation,
)
from autoskillit.server.tools._authority_feedback import (
    build_authority_clobber_warnings,
    build_authority_rejection_envelope,
)
from autoskillit.server.tools._auto_overrides import (
    _compute_effective_backend_map,
)
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._overlay_state import (
    OverlayStateError,
    locked_overlay,
    read_overlay,
    update_overlay,
)
from autoskillit.server.tools._pipeline_deps import _derive_phase_a_deps
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

logger = get_logger(__name__)

_PR_CREATE_RECIPES: frozenset[str] = frozenset(
    {"merge-prs", "implementation", "implementation-groups", "remediation"}
)
_OPEN_KITCHEN_REQUEST_CTX: ContextVar[ToolContext] = ContextVar("open_kitchen_request_context")


def _ensure_kitchen_transition(tool_ctx: ToolContext) -> None:
    """Create infrastructure identity once, before request arguments are bound."""
    with tool_ctx.kitchen_transition_lock:
        state = tool_ctx.kitchen_open_state
        if state.phase is KitchenOpenPhase.CLOSED:
            state = new_kitchen_open_state(
                kitchen_id=resolve_kitchen_id(),
                context_id=state.context_id,
            )
            tool_ctx.kitchen_open_state = state
        tool_ctx.kitchen_id = state.kitchen_id


def _transition_start(tool_ctx: ToolContext, name: str) -> bool:
    """Journal STARTED and report whether the effect still needs dispatch."""
    with tool_ctx.kitchen_transition_lock:
        existing = next(
            (effect for effect in tool_ctx.kitchen_open_state.effects if effect.name == name),
            None,
        )
        if existing is not None:
            if existing.phase in {
                KitchenEffectPhase.STARTED,
                KitchenEffectPhase.CONFIRMED,
                KitchenEffectPhase.DEGRADED,
            }:
                return False
            if existing.phase is KitchenEffectPhase.AMBIGUOUS:
                raise RuntimeError(f"kitchen effect {name!r} requires reconciliation")
        tool_ctx.kitchen_open_state = start_kitchen_effect(
            tool_ctx.kitchen_open_state,
            name,
        )
    return True


def _transition_fields(tool_ctx: ToolContext, *, committed: bool = False) -> dict[str, Any]:
    if committed:
        _transition_start(tool_ctx, KITCHEN_EFFECT_RESPONSE_ENFORCEMENT)
    with tool_ctx.kitchen_transition_lock:
        payload = kitchen_state_payload(tool_ctx.kitchen_open_state)
    if committed:
        payload["phase"] = KitchenOpenPhase.COMMITTED.value
        payload["retry_disposition"] = KitchenRetryDisposition.COMMITTED_REPLAY.value
        for effect in payload["effects"]:
            if effect["phase"] == KitchenEffectPhase.STARTED.value:
                effect["phase"] = KitchenEffectPhase.CONFIRMED.value
                effect["receipt"] = f"response:{effect['effect_id']}"
    return payload


def _attach_transition_fields(
    result: dict[str, Any],
    tool_ctx: ToolContext,
    *,
    committed: bool,
) -> dict[str, Any]:
    result.update(_transition_fields(tool_ctx, committed=committed))
    return result


def _open_kitchen_conflict_response(
    conflict: KitchenIntentConflict,
) -> str:
    payload = kitchen_state_payload(conflict.state)
    payload.update(
        {
            "success": False,
            "kitchen": "failed",
            "error": "open_kitchen_intent_fingerprint_conflict",
            "received_intent_fingerprint": conflict.received_fingerprint,
            "retry_disposition": KitchenRetryDisposition.FINGERPRINT_CONFLICT.value,
        }
    )
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _read_open_kitchen_request_ctx() -> ToolContext:
    return _OPEN_KITCHEN_REQUEST_CTX.get()


def _open_kitchen_cancellation_response(
    tool_ctx: ToolContext,
    exc: BaseException,
) -> str:
    with tool_ctx.kitchen_transition_lock:
        state = tool_ctx.kitchen_open_state
        started = next(
            (
                effect
                for effect in reversed(state.effects)
                if effect.phase is KitchenEffectPhase.STARTED
            ),
            None,
        )
        if started is not None:
            state = mark_kitchen_effect_ambiguous(
                state,
                started.name,
                evidence=f"{type(exc).__name__}: transport teardown",
            )
            tool_ctx.kitchen_open_state = state
        payload = kitchen_state_payload(state)
    payload.update(
        {
            "success": False,
            "kitchen": "failed",
            "error": "cancelled",
            "subtype": "cancelled",
        }
    )
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _bind_open_kitchen_transition(
    fn: Callable[..., Awaitable[str]],
) -> Callable[..., Awaitable[str]]:
    """Bind request intent outside the typed cancellation boundary."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        from autoskillit.server import _get_ctx  # circular-break

        try:
            tool_ctx = _get_ctx()
        except RuntimeError:
            unshielded = cast(
                Callable[..., Awaitable[str]],
                getattr(fn, "__wrapped__", fn),
            )
            return await unshielded(*args, **kwargs)
        _ensure_kitchen_transition(tool_ctx)
        bound = signature.bind_partial(*args, **kwargs)
        name = bound.arguments.get("name")
        overrides = bound.arguments.get("overrides")
        ingredients_only = bool(bound.arguments.get("ingredients_only", False))
        delivery_request = bound.arguments.get("delivery_request")
        fingerprint = canonical_kitchen_intent_fingerprint(
            name=name if isinstance(name, str) else None,
            overrides=overrides if isinstance(overrides, Mapping) else None,
            ingredients_only=ingredients_only,
            delivery_request=(delivery_request if isinstance(delivery_request, Mapping) else None),
            context_id=tool_ctx.kitchen_open_state.context_id,
        )
        mode = "ingredients_only" if ingredients_only else ("recipe" if name else "anonymous")
        with tool_ctx.kitchen_transition_lock:
            active = tool_ctx.kitchen_open_state
            committed_postconditions_hold = not (
                active.phase is KitchenOpenPhase.COMMITTED
                and mode == "recipe"
                and getattr(tool_ctx, "recipe_name", "") != name
            )
            if (
                active.phase is KitchenOpenPhase.COMMITTED
                and active.intent_fingerprint is not None
                and (active.intent_fingerprint != fingerprint or not committed_postconditions_hold)
            ):
                tool_ctx.kitchen_open_state = new_kitchen_open_state(
                    kitchen_id=active.kitchen_id,
                    context_id=active.context_id,
                )
        try:
            with tool_ctx.kitchen_transition_lock:
                tool_ctx.kitchen_open_state = bind_kitchen_intent(
                    tool_ctx.kitchen_open_state,
                    fingerprint=fingerprint,
                )
                state = tool_ctx.kitchen_open_state
        except KitchenIntentConflict as conflict:
            return _open_kitchen_conflict_response(conflict)
        if state.phase is KitchenOpenPhase.COMMITTED and state.cached_response is not None:
            return state.cached_response
        if state.retry_disposition is KitchenRetryDisposition.RECONCILE_REQUIRED:
            payload = kitchen_state_payload(state)
            payload.update(
                {
                    "success": False,
                    "kitchen": "failed",
                    "error": "open_kitchen_reconciliation_required",
                }
            )
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        with tool_ctx.kitchen_transition_lock:
            state, claimed = claim_kitchen_request(tool_ctx.kitchen_open_state)
            tool_ctx.kitchen_open_state = state
        if not claimed:
            payload = kitchen_state_payload(state)
            payload.update(
                {
                    "success": False,
                    "kitchen": "in_progress",
                    "error": "open_kitchen_in_progress",
                }
            )
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        try:
            token = _OPEN_KITCHEN_REQUEST_CTX.set(tool_ctx)
            try:
                result = await fn(*args, **kwargs)
            finally:
                _OPEN_KITCHEN_REQUEST_CTX.reset(token)

            parsed: dict[str, Any] | None
            try:
                candidate = json.loads(result)
                parsed = candidate if isinstance(candidate, dict) else None
            except (TypeError, json.JSONDecodeError):
                parsed = None
            if parsed is not None and parsed.get("success") is True:
                initialization_id = parsed.get("initialization_id")
                with tool_ctx.kitchen_transition_lock:
                    state = tool_ctx.kitchen_open_state
                    for effect in state.effects:
                        if effect.phase is KitchenEffectPhase.STARTED:
                            state = confirm_kitchen_effect(
                                state,
                                effect.name,
                                receipt=f"response:{effect.effect_id}",
                            )
                    tool_ctx.kitchen_open_state = commit_kitchen_response(
                        state,
                        response=result,
                        initialization_id=(
                            initialization_id if isinstance(initialization_id, str) else None
                        ),
                    )
                return result
            if parsed is not None:
                with tool_ctx.kitchen_transition_lock:
                    state = tool_ctx.kitchen_open_state
                    started = next(
                        (
                            effect
                            for effect in reversed(state.effects)
                            if effect.phase is KitchenEffectPhase.STARTED
                        ),
                        None,
                    )
                    if started is not None:
                        state = mark_kitchen_effect_ambiguous(
                            state,
                            started.name,
                            evidence=f"application failure after {started.name} dispatch",
                        )
                        tool_ctx.kitchen_open_state = state
                parsed.update(_transition_fields(tool_ctx))
                return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            return result
        finally:
            with tool_ctx.kitchen_transition_lock:
                tool_ctx.kitchen_open_state = release_kitchen_request(tool_ctx.kitchen_open_state)

    return wrapper


def _kitchen_failure_envelope(
    exc: BaseException,
    stage: str,
    *,
    user_hint: str | None = None,
) -> str:
    """Return a JSON failure envelope for open_kitchen errors.

    Tool implementations catch exceptions locally and emit domain-specific
    envelopes with helpful ``user_visible_message`` values; the
    ``@track_response_size`` decorator only catches what slips through.
    """
    msg = user_hint or (
        f"open_kitchen failed during {stage}: {type(exc).__name__}. "
        f"Run 'autoskillit doctor' to diagnose, "
        f"or run 'autoskillit install' if the failure persists."
    )
    payload: dict[str, Any] = {
        "success": False,
        "kitchen": "failed",
        "user_visible_message": msg,
        "error": f"{type(exc).__name__}: {exc}",
        "stage": stage,
    }
    try:
        from autoskillit.server._state import _get_ctx_or_none  # circular-break

        tool_ctx = _get_ctx_or_none()
        if tool_ctx is not None:
            payload.update(_transition_fields(tool_ctx))
    except Exception:
        logger.warning("open_kitchen_transition_failure_envelope_failed", exc_info=True)
    return json.dumps(payload)


def _recipe_validation_error_response(name: str, result: dict[str, Any]) -> str:
    _structural_errs: list[str] = result.get("errors", [])
    if _structural_errs:
        _error_parts = _structural_errs[:3]
        if len(_structural_errs) > 3:
            _error_parts.append(f"+{len(_structural_errs) - 3} more errors")
    else:
        _all_errors = []
        for s in result.get("suggestions", []):
            if isinstance(s, dict) and s.get("severity") == "error":
                _line = f"[{s.get('rule', 'unknown-rule')}] {s.get('message', '')}"
                if s.get("origin"):
                    _line += f" (origin: {s['origin']})"
                if s.get("remedy"):
                    _line += f" — remedy: {s['remedy']}"
                _all_errors.append(_line)
        _error_parts = _all_errors[:3]
        if len(_all_errors) > 3:
            _error_parts.append(f"+{len(_all_errors) - 3} more errors")
    _error_detail = "; ".join(_error_parts) if _error_parts else "unknown structural error"
    _label = "structural validation" if _structural_errs else "validation"
    return json.dumps(
        {
            "success": False,
            "kitchen": "failed",
            "user_visible_message": (f"Recipe '{name}' failed {_label}: {_error_detail}"),
            "error": f"Recipe '{name}' failed validation: {_error_detail}",
            "stage": "recipe_validation",
            "errors": _structural_errs,
            "suggestions": result.get("suggestions", []),
        }
    )


class QuotaGuardHookPayload(TypedDict):
    cache_max_age: int
    cache_path: str
    buffer_seconds: int
    disabled: bool


class OutputBudgetPolicyHookPayload(TypedDict):
    disabled: bool
    shell_max_inline_bytes: int
    capture_capacity: NotRequired[dict[str, int]]


def _quota_guard_hook_payload(cfg: QuotaGuardConfig) -> QuotaGuardHookPayload:
    """Return the quota_guard section of .hook_config.json for a given config.

    This is the single authoritative definition of which QuotaGuardConfig fields
    cross the stdlib-only boundary into hook subprocesses. When adding a field to
    QuotaHookSettings, add the corresponding source field here AND update
    QUOTA_GUARD_HOOK_PAYLOAD_KEYS in _hook_settings.py. The contract test
    test_hook_bridge_coverage.py enforces that both stay in sync.
    """
    return {
        "cache_max_age": cfg.cache_max_age,
        "cache_path": cfg.cache_path,
        "buffer_seconds": cfg.buffer_seconds,
        "disabled": not cfg.enabled,
    }


def _output_budget_policy_hook_payload(
    cfg: OutputBudgetConfig,
) -> OutputBudgetPolicyHookPayload:
    """Return the output-budget guard section of ``.hook_config.json``.

    Keep these keys in sync with ``OUTPUT_BUDGET_POLICY_HOOK_PAYLOAD_KEYS``
    in the stdlib-only hook settings bridge.
    """
    payload: OutputBudgetPolicyHookPayload = {
        "disabled": not cfg.guard_enabled,
        "shell_max_inline_bytes": cfg.shell_max_inline_bytes,
    }
    if cfg.capture_capacity is not None:
        payload["capture_capacity"] = cfg.capture_capacity
    return payload


def _write_hook_config() -> None:
    """Write hook policy snapshots to .autoskillit/temp/.hook_config.json.

    Hook subprocesses read this file to apply user settings without importing
    the autoskillit package.
    """
    from autoskillit.server import _get_ctx, logger  # circular-break

    ctx = _get_ctx()
    response_temp_root = (
        ctx.temp_dir
        if isinstance(getattr(ctx, "temp_dir", None), Path)
        else ctx.project_dir / ".autoskillit" / "temp"
    )
    payload = {
        "quota_guard": _quota_guard_hook_payload(ctx.config.quota_guard),
        "output_budget_policy": _output_budget_policy_hook_payload(ctx.config.output_budget),
        "response_temp_root": str(response_temp_root.resolve()),
        "kitchen_id": ctx.kitchen_id,
        "git_ops_policy": {},
    }
    hook_cfg_path = _hook_config_path(ctx.project_dir)
    try:
        hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(hook_cfg_path, json.dumps(payload))
    except OSError:
        logger.warning("hook_config_write_failed", path=str(hook_cfg_path))


def _update_hook_config_with_recipe() -> None:
    """Enrich .hook_config.json with recipe-level authorization after recipe loading."""
    from autoskillit.server import _get_ctx, logger  # circular-break

    ctx = _get_ctx()
    hook_cfg_path = _hook_config_path(ctx.project_dir)
    try:
        payload = json.loads(hook_cfg_path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("hook_config_recipe_update_read_failed", path=str(hook_cfg_path))
        return
    if ctx.recipe_name in _PR_CREATE_RECIPES:
        payload["recipe_allows_pr_create"] = True
    try:
        atomic_write(hook_cfg_path, json.dumps(payload))
    except OSError:
        logger.warning("hook_config_recipe_update_write_failed", path=str(hook_cfg_path))


def _update_hook_config_with_git_ops_policy() -> None:
    """Propagate recipe-level git_ops_policy overlay to .hook_config.json.

    Reads the overlay from the hook config overlay file and merges it into the
    base config's git_ops_policy dict. Currently no recipe sets this; the
    mechanism exists for future recipes that legitimately need destructive git ops
    (e.g. allow_push for a release automation recipe).
    """
    from autoskillit.server import _get_ctx, logger  # circular-break

    ctx = _get_ctx()
    hook_cfg_path = _hook_config_path(ctx.project_dir)
    try:
        payload = json.loads(hook_cfg_path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("hook_config_git_ops_policy_update_read_failed", path=str(hook_cfg_path))
        return
    git_ops_policy: dict = payload.get("git_ops_policy", {})
    try:
        overlay_policy = read_overlay(ctx.project_dir).get("git_ops_policy", {})
    except (OSError, OverlayStateError):
        logger.warning("hook_config_git_ops_policy_overlay_invalid", exc_info=True)
        return
    if overlay_policy:
        git_ops_policy = {**git_ops_policy, **overlay_policy}
    payload["git_ops_policy"] = git_ops_policy
    try:
        atomic_write(hook_cfg_path, json.dumps(payload))
    except OSError:
        logger.warning("hook_config_git_ops_policy_update_write_failed", path=str(hook_cfg_path))


def _retain_kitchen_tracker_authority(
    tool_ctx: ToolContext,
) -> tuple[TrackerParticipantKey, ArtifactLease]:
    """Retain this process incarnation's kitchen tracker lease."""
    target = TrackerAuthorityTarget.for_project(
        tool_ctx.project_dir,
        tool_ctx.kitchen_id,
        expected=False,
    )
    with tool_ctx.tracker_leases_lock:
        identity = get_kitchen_process_identity(tool_ctx)
        key = TrackerParticipantKey(
            target=target,
            owner_kind="kitchen",
            owner_id=identity.kitchen_id,
            pid=identity.pid,
            create_time=identity.create_time,
            project_path=identity.project_path,
        )
        lease = retain_tracker_lease(tool_ctx.tracker_leases, key)
        tool_ctx.kitchen_tracker_key = key
    return key, lease


def _release_kitchen_tracker_authority(
    tool_ctx: ToolContext,
    *,
    unregister: bool,
    retire: bool,
) -> None:
    """Release exact ToolContext ownership and optionally retire its tracker."""
    with tool_ctx.tracker_leases_lock:
        key = tool_ctx.kitchen_tracker_key
        identity = tool_ctx.kitchen_process_identity
        if key is not None:
            release_tracker_lease(tool_ctx.tracker_leases, key)
        tool_ctx.kitchen_tracker_key = None
        if unregister:
            tool_ctx.kitchen_process_identity = None
    try:
        if unregister and identity is not None:
            unregister_active_kitchen(identity)
    finally:
        if retire and key is not None:
            try_retire_tracker(key.target)


def prune_stale_kitchen_state(project_dir: Path, current_kitchen_id: str) -> None:
    """Offer each foreign tracker to the core retirement authority."""
    tracker_dir = pipeline_tracker_directory(project_dir)
    if not tracker_dir.is_dir():
        return

    for tracker_file in tracker_dir.glob("*.json"):
        if tracker_file.name.startswith(".") or tracker_file.stem == current_kitchen_id:
            continue
        try:
            target = TrackerAuthorityTarget.for_project(
                project_dir,
                tracker_file.stem,
                expected=False,
            )
        except ValueError as exc:
            logger.warning(
                "invalid_stale_tracker_candidate",
                path=str(tracker_file),
                error=str(exc),
            )
            continue
        try_retire_tracker(target)


def _auto_init_pipeline_tracker(tool_ctx: ToolContext) -> str | None:
    """Auto-derive and initialize the kitchen-scoped pipeline dependency tracker.

    Self-arming, server-internal counterpart to ``record_pipeline_step(op="init")``
    — runs at ``open_kitchen`` time from ``ctx.active_recipe_steps``, requiring
    no LLM action, mirroring how ingredient locks are primed. The core authority
    seam performs the locked merge while this caller retains the kitchen lease.

    Idempotent across the deferred-override re-call pattern: an existing
    tracker's step statuses and previously-tracked dependency keys are
    preserved rather than overwritten.
    """
    active_steps = tool_ctx.active_recipe_steps
    if not active_steps:
        return None
    try:
        deps = _derive_phase_a_deps(active_steps)
    except Exception:
        logger.warning("pipeline_tracker_auto_init_deps_failed", exc_info=True)
        return None
    if not deps:
        return None

    key, lease = _retain_kitchen_tracker_authority(tool_ctx)
    steps: dict[str, dict[str, str]] = {name: {"status": "pending"} for name in active_steps}
    dependencies: dict[str, list[str]] = dict(deps)

    tracker_data = {
        "kitchen_id": tool_ctx.kitchen_id,
        "pipeline_id": tool_ctx.kitchen_id,
        "steps": steps,
        "dependencies": dependencies,
        "initialized_at": datetime.now(UTC).isoformat(),
    }
    try:
        result = initialize_kitchen_tracker(key.target, lease, tracker_data)
    except Exception:
        _release_kitchen_tracker_authority(tool_ctx, unregister=False, retire=False)
        raise
    if result.error is not None:
        _release_kitchen_tracker_authority(tool_ctx, unregister=False, retire=False)
    return result.error


def _pipeline_tracker_auto_init_failure(tool_ctx: ToolContext, error: str) -> str:
    """Abort kitchen opening after tracker initialization fails."""
    transition_abort(tool_ctx, KITCHEN_EFFECT_RECIPE_SERVING)
    tool_ctx.gate.disable()
    tool_ctx.gate_infrastructure_ready = False
    return _kitchen_failure_envelope(
        RuntimeError(error),
        stage="pipeline_tracker_auto_init",
        user_hint=error,
    )


async def _open_kitchen_handler(*, preserve_active_recipe: bool = False) -> str | None:
    """Set the tools-enabled flag. Extracted for testability.

    Returns ``None`` on success, or a JSON failure envelope string on error.
    """
    from autoskillit.server import _get_ctx, logger  # circular-break

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


def _close_kitchen_handler() -> None:
    """Clear the tools-enabled flag. Extracted for testability."""
    from autoskillit.server import _get_ctx, logger  # circular-break

    ctx = _get_ctx()
    authority = ctx.run_skill_completion
    if authority is not None and not authority.clear_if_idle():
        raise RuntimeError("run_skill completion is still active")
    if ctx.quota_refresh_task is not None:
        ctx.quota_refresh_task.cancel()
        ctx.quota_refresh_task = None
    baseline_config = deepcopy(ctx._baseline_config)
    baseline_lock = FleetSemaphore(
        max_concurrent=baseline_config.fleet.max_concurrent_dispatches,
        timeout=baseline_config.fleet.acquire_timeout_sec,
    )
    hook_cfg_path = _hook_config_path(ctx.project_dir)
    with locked_overlay(ctx.project_dir) as (overlay_path, _):
        ctx.gate.disable()
        try:
            hook_cfg_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("hook_config_remove_failed", path=str(hook_cfg_path))
        try:
            overlay_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("hook_config_overlay_remove_failed", path=str(overlay_path))
        ctx._session_config_overrides.clear()
        ctx.config = baseline_config
        ctx.fleet_lock = baseline_lock
    try:
        _release_kitchen_tracker_authority(ctx, unregister=True, retire=True)
    except Exception:
        logger.warning("close_kitchen_tracker_authority_release_failed", exc_info=True)
    with ctx.tracker_leases_lock:
        abandoned_targets = {key.target for key in ctx.tracker_leases}
        for key in list(ctx.tracker_leases):
            release_tracker_lease(ctx.tracker_leases, key)
    for target in abandoned_targets:
        try_retire_tracker(target)
    if isinstance(ctx.kitchen_id, str) and ctx.kitchen_id:
        if isinstance(ctx.temp_dir, Path) and not retire_recipe_artifacts(
            ctx.temp_dir,
            kitchen_id=ctx.kitchen_id,
        ):
            logger.warning("close_kitchen_recipe_artifact_retirement_failed")
        try:
            retire_recipe_generation(ctx.kitchen_id)
        except Exception:
            logger.warning("close_kitchen_recipe_generation_retirement_failed", exc_info=True)
    ctx.active_recipe_packs = None
    ctx.active_recipe_features = None
    ctx.active_recipe_steps = None
    ctx.active_recipe_ingredients = None
    ctx.session_serve_overrides = None
    ctx.session_serve_defer_unresolved = False
    ctx.recipe_name = ""
    ctx.recipe_content_hash = ""
    ctx.recipe_composite_hash = ""
    ctx.recipe_version = ""
    clear_recipe_execution(ctx)
    ctx.gate_infrastructure_ready = False
    logger.info("close_kitchen", gate_state="closed")
    if (log := ctx.github_api_log) is not None:
        orphan_usage = log.drain(ctx.kitchen_id)
        if orphan_usage is not None:
            try:
                log_dir = resolve_log_dir(ctx.config.linux_tracing.log_dir)
                orphan_path = log_dir / "github_api_usage_orchestrator.json"
                atomic_write(orphan_path, fast_dumps(orphan_usage))
            except Exception:
                logger.warning("close_kitchen_orphan_drain_failed", exc_info=True)
    review_gate_path = ctx.project_dir / ".autoskillit" / "temp" / "review_gate_state.json"
    try:
        try:
            state = json.loads(review_gate_path.read_text())
            loop_active = (
                isinstance(state, dict)
                and state.get("gate") == "LOOP_REQUIRED"
                and not state.get("check_review_loop_called", False)
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug(
                "review_gate_state_read_failed", path=str(review_gate_path), error=str(exc)
            )
            loop_active = False
        if loop_active:
            logger.warning(
                "close_kitchen_review_gate_preserved",
                path=str(review_gate_path),
                reason="active_review_loop",
            )
        else:
            review_gate_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("review_gate_state_remove_failed", path=str(review_gate_path))
    with ctx.kitchen_transition_lock:
        context_id = ctx.kitchen_open_state.context_id
        ctx.kitchen_open_state = closed_kitchen_open_state(context_id=context_id)
        ctx.kitchen_id = ""


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
        _defaults = resolve_ingredient_defaults(ctx.project_dir)
        _config_layer = build_config_authoritative_layer(_defaults)
        _session_overrides: dict[str, str] = {
            "kitchen_id": ctx.kitchen_id,
            "diagnostics_log_dir": str(resolve_log_dir(ctx.config.linux_tracing.log_dir)),
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
        _config_default = build_config_default_layer(_defaults)
        result = serve_recipe(
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
    finalized = finalize_recipe_delivery(
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
        _attach_transition_fields(inspection, tool_ctx, committed=True)
    return render_served_response(inspection)


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


@mcp.tool(
    tags={"autoskillit"}, annotations={"readOnlyHint": True}, meta={"anthropic/alwaysLoad": True}
)
@_cancellation_shield()
@track_response_size("close_kitchen")
async def close_kitchen(ctx: Context = CurrentContext()) -> str:
    """Close the AutoSkillit kitchen.

    Never raises.
    """
    try:
        if (h := _require_orchestrator_exact("close_kitchen")) is not None:
            return h
        _close_kitchen_handler()
        from autoskillit.server import _get_ctx  # circular-break: server lifecycle owner

        exploration_store = _get_ctx().exploration_context_store
        if exploration_store is not None:
            exploration_store.close()

        mcp.disable(tags={"kitchen"})
        mcp.disable(tags={"exploration"})
        mcp.disable(tags={"plan-review"})

        await ctx.reset_visibility()
        return "Kitchen is closed."
    except Exception as exc:
        logger.error("close_kitchen unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(
    tags={"autoskillit"}, annotations={"readOnlyHint": True}, meta={"anthropic/alwaysLoad": True}
)
@_cancellation_shield()
@track_response_size("disable_quota_guard")
async def disable_quota_guard() -> str:
    """Disable the quota guard for the remainder of this kitchen session.

    The quota guard blocks run_skill calls when API utilization exceeds a
    threshold. Invoke this tool when you decide the work is worth the quota
    spend and want to override the guard for the current session.

    The caller-session disable is recorded by a PostToolUse hook that reads
    the caller's ``session_id`` from the hook event. The MCP tool itself
    only enforces the local-server lifecycle (orchestrator-exact guard,
    open-kitchen gate) and returns success. The hook writes the marker
    immediately after this response is rendered.

    Session-scoped only: the guard re-activates when the kitchen is closed
    and reopened. Does not modify persistent configuration.

    Never raises.
    """
    try:
        if (h := _require_orchestrator_exact("disable_quota_guard")) is not None:
            return h
        from autoskillit.server import _get_ctx  # circular-break

        ctx = _get_ctx()
        if not ctx.gate.enabled:
            return json.dumps(
                {
                    "success": False,
                    "error": "Kitchen is not open — gate is closed.",
                }
            )
        return json.dumps(
            {
                "success": True,
                "content": (
                    "Quota guard disabled for this session. "
                    "run_skill calls will no longer be blocked by quota checks."
                ),
            }
        )
    except Exception as exc:
        logger.error("disable_quota_guard unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


def _write_ingredient_locks(
    project_dir: Path,
    pipeline_id: str,
    new_locked: dict[str, str] | None,
    unlock_keys: list[str] | None,
    active_steps: dict,
) -> dict:
    """Atomically read-modify-write ingredient locks under the session lock."""

    def _mutate(existing: dict) -> None:
        locked_ingredients = existing.setdefault("locked_ingredients", {})
        current = dict(locked_ingredients.get(pipeline_id, {}))
        if unlock_keys:
            _apply_unlock_keys(current, unlock_keys)
        if new_locked:
            current.update(new_locked)
        if new_locked or unlock_keys:
            locked_ingredients[pipeline_id] = current
            existing.setdefault("locked_steps", {})[pipeline_id] = _compute_unlocked_steps(
                active_steps,
                current,
            )

    return update_overlay(project_dir, _mutate)


def _compute_unlocked_steps(
    active_steps: dict, current_pipeline_li: dict[str, str]
) -> dict[str, bool]:
    """Compute unlocked_steps from active_recipe_steps and remaining ingredients.

    For each step with a skip_when_false ingredient present in current_pipeline_li,
    compute the truthiness of the remaining ingredient value.
    """
    unlocked_steps: dict[str, bool] = {}
    for step_name, step_obj in active_steps.items():
        swf = (
            getattr(step_obj, "skip_when_false", None)
            if hasattr(step_obj, "skip_when_false")
            else None
        )
        if swf:
            ingredient_name = swf.removeprefix("inputs.")
            if ingredient_name in current_pipeline_li:
                val = current_pipeline_li[ingredient_name]
                is_truthy = val.lower() not in ("false", "0", "no", "off", "")
                unlocked_steps[step_name] = is_truthy
    return unlocked_steps


def _apply_unlock_keys(current_pipeline_li: dict[str, str], unlock_keys: list[str]) -> None:
    """Remove unlock keys from the current pipeline ingredients dict in-place."""
    for key in unlock_keys:
        current_pipeline_li.pop(key, None)


def _build_ingredient_key_suggestions(
    unknown: set[str], declared: frozenset[str]
) -> dict[str, list[str]]:
    suggestions: dict[str, list[str]] = {}
    declared_sorted = sorted(declared)
    for key in sorted(unknown):
        matches = difflib.get_close_matches(key, declared_sorted, n=2, cutoff=0.5)
        if matches:
            suggestions[key] = list(matches)
    return suggestions


@mcp.tool(
    tags={"autoskillit"}, annotations={"readOnlyHint": True}, meta={"anthropic/alwaysLoad": True}
)
@_cancellation_shield()
@track_response_size("lock_ingredients")
async def lock_ingredients(
    locked: dict[str, str] | None = None,
    pipeline_id: str = "",
    unlock: list[str] | None = None,
) -> str:
    """Lock recipe ingredient values for this session.

    Call at session start to bind ingredient values structurally.
    Locked ingredients are enforced by a server-side check in run_skill
    and supplementally by the ingredient_lock_guard PreToolUse hook.
    run_skill calls for steps whose skip_when_false ingredient is locked
    to a falsy value will be denied.

    Server-authoritative ingredients (base_branch, local_review_rounds,
    adversarial_review_level, is_fleet_dispatch,
    dispatch_id) are rejected with a structured error envelope; the
    rejected key names appear in both ``error`` and ``user_visible_message``.

    Call with unlock=["ingredient_name"] to release a lock.

    Never raises.
    """
    try:
        if (h := _require_orchestrator_exact("lock_ingredients")) is not None:
            return h
        from autoskillit.server import _get_ctx  # circular-break

        ctx = _get_ctx()
        hook_cfg_path = _hook_config_path(ctx.project_dir)
        if not hook_cfg_path.exists():
            return json.dumps(
                {
                    "success": False,
                    "error": "Kitchen is not open — hook config file absent.",
                }
            )
        effective_pipeline_id = pipeline_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

        if locked:
            server_auth_overlap = set(locked.keys()) & SERVER_AUTHORITATIVE_INGREDIENTS
            if server_auth_overlap:
                return json.dumps(build_authority_rejection_envelope(server_auth_overlap))

        if not locked and not unlock:
            return json.dumps(
                {
                    "success": False,
                    "error": "At least one of 'locked' or 'unlock' must be provided.",
                }
            )

        active_steps = getattr(ctx, "active_recipe_steps", None) or {}
        declared_ingredients = ctx.active_recipe_ingredients
        if declared_ingredients is not None:
            all_supplied_keys: set[str] = set()
            if locked:
                all_supplied_keys |= set(locked.keys())
            if unlock:
                all_supplied_keys |= set(unlock)
            unknown = all_supplied_keys - declared_ingredients - SERVER_AUTHORITATIVE_INGREDIENTS
            if unknown:
                return json.dumps(
                    {
                        "success": False,
                        "error": (
                            f"Unknown ingredient keys: {sorted(unknown)}. "
                            f"Valid keys: {sorted(declared_ingredients)}."
                        ),
                        "suggestions": _build_ingredient_key_suggestions(
                            unknown, declared_ingredients
                        ),
                    }
                )

        updated = _write_ingredient_locks(
            ctx.project_dir,
            effective_pipeline_id,
            locked,
            unlock,
            active_steps,
        )

        return json.dumps(
            {
                "success": True,
                "locked": updated.get("locked_ingredients", {}).get(effective_pipeline_id, {}),
                "locked_steps": updated.get("locked_steps", {}).get(effective_pipeline_id, {}),
            }
        )
    except Exception as exc:
        logger.error("lock_ingredients unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


def _find_session_id_for_reload(cwd: Path) -> str | None:
    """Return the session_id to use for reload; kitchen marker preferred, mtime fallback."""
    state_dir = get_state_dir()
    if state_dir.is_dir():

        def _safe_mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0

        candidates = sorted(state_dir.glob("*.json"), key=_safe_mtime, reverse=True)
        for p in candidates:
            marker = read_marker(p.stem)
            if marker is not None and is_marker_fresh(marker):
                return marker.session_id
    return find_latest_session_id(str(cwd))


def _write_reload_sentinel(cwd: Path, session_id: str) -> None:
    """Atomically write a reload sentinel file for session_id."""
    sentinel_path = cwd / ".autoskillit" / "temp" / "reload_sentinel" / f"{session_id}.json"
    payload = json.dumps({"session_id": session_id, "requested_at": datetime.now(UTC).isoformat()})
    atomic_write(sentinel_path, payload)


def _reload_session_handler() -> dict[str, str]:
    """Core logic for the reload_session tool — testable without FastMCP."""
    cwd = Path.cwd()
    session_id = _find_session_id_for_reload(cwd)
    if not session_id:
        raise ValueError(
            "Cannot determine session ID. Ensure open_kitchen was called, "
            "or that a Claude Code session JSONL exists for this project."
        )
    _write_reload_sentinel(cwd, session_id)
    return {
        "status": "reload_requested",
        "session_id": session_id,
        "next_action": (
            "Run /exit now. The parent autoskillit process will re-launch "
            "with --resume and full wrapper environment."
        ),
    }


@mcp.tool(
    tags={"autoskillit"}, annotations={"readOnlyHint": True}, meta={"anthropic/alwaysLoad": True}
)
@_cancellation_shield()
@track_response_size("reload_session")
async def reload_session() -> str:
    """Signal the parent autoskillit process to reload this session with the full
    wrapper environment intact and resume the conversation.

    After calling this tool, run /exit to allow the parent process to detect the
    reload request and re-launch claude with --resume <session_id>.

    Never raises.
    """
    try:
        return json.dumps(_reload_session_handler())
    except Exception as exc:
        logger.error("reload_session unhandled exception", exc_info=True)
        return json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"})


def _declare_join_batch_handler(
    skill_name: str,
    assignments: list[str],
    session_id: str,
    top_level_parent: str | None = None,
) -> dict[str, object]:
    """Core logic for the declare_join_batch tool — testable without FastMCP."""
    from autoskillit.execution.backends import get_backend
    from autoskillit.hooks._join_ledger import JoinLedgerError, declare_batch

    flag_dir = Path.cwd() / ".autoskillit" / "temp"
    flag_dir.mkdir(parents=True, exist_ok=True)
    flag_path = flag_dir / f"skill_guard_{session_id}.flag"
    binding: dict[str, object] = {}
    if flag_path.exists():
        try:
            binding = json.loads(flag_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            binding = {}
    if not isinstance(binding, dict):
        binding = {}

    # Fail-closed validation: a join-bearing session binding, a loaded skill
    # entry, and the backend's fixed-set-join capability must all line up
    # before we open a wave.
    if not bool(binding.get("join_required", False)):
        return {
            "success": False,
            "error": "declare_join_batch requires a join-bearing session binding",
        }
    loaded = binding.get("loaded_skills", [])
    if not isinstance(loaded, list) or not any(
        isinstance(entry, dict) and entry.get("skill_name") == skill_name for entry in loaded
    ):
        return {
            "success": False,
            "error": f"declare_join_batch: skill {skill_name!r} is not loaded in this session",
        }
    backend_name = (
        os.environ.get("AUTOSKILLIT_AGENT_BACKEND", "claude-code").strip() or "claude-code"
    )
    backend = None
    try:
        backend = get_backend(backend_name)
    except Exception:
        backend = None
    if backend is None or not getattr(backend.capabilities, "fixed_set_join_capable", False):
        return {
            "success": False,
            "error": (
                f"declare_join_batch: backend {backend_name!r} does not attest "
                "fixed_set_join_capable"
            ),
        }
    # Check backend supports the requested assignments against the manifest's
    # declared child_spawn_cardinality.
    manifest_cardinality: dict[str, object] = {}
    for entry in loaded:
        if isinstance(entry, dict) and entry.get("skill_name") == skill_name:
            card = entry.get("child_spawn_cardinality", {})
            if isinstance(card, dict):
                manifest_cardinality = card
            break
    declared_count: object | None = None
    for spawn in manifest_cardinality.values():
        declared_count = spawn
        break
    if declared_count is not None and isinstance(declared_count, int):
        if len(assignments) != declared_count:
            return {
                "success": False,
                "error": (
                    f"declare_join_batch: skill {skill_name!r} declares "
                    f"count={declared_count}; received {len(assignments)} assignments"
                ),
            }

    artifact_digest = str(binding.get("artifact_digest", "")) or _derive_artifact_digest(binding)
    parent = top_level_parent or "top_level"
    try:
        batch = declare_batch(
            flag_dir,
            session_id=session_id,
            top_level_parent=parent,
            skill_name=skill_name,
            artifact_digest=artifact_digest,
            assignments=assignments,
        )
    except JoinLedgerError as exc:
        _emit_join_diagnostic(
            {
                "gate": "declare_join_batch",
                "session_id": session_id,
                "top_level_parent": parent,
                "skill_name": skill_name,
                "status": "declare_refused",
            }
        )
        return {"success": False, "error": str(exc)}
    _emit_join_diagnostic(
        {
            "gate": "declare_join_batch",
            "session_id": session_id,
            "top_level_parent": parent,
            "join_batch_id": batch.get("join_batch_id", ""),
            "skill_name": skill_name,
            "status": "declared",
        }
    )
    return {"success": True, "join_batch_id": batch.get("join_batch_id"), "wave": batch}


def _emit_join_diagnostic(record: dict[str, object]) -> None:
    """Bounded MCP-side diagnostic emission. Falls back to stderr on failure."""
    allowed_keys = {
        "ts",
        "session_id",
        "top_level_parent",
        "join_batch_id",
        "skill_name",
        "status",
        "selector_presence",
    }
    bounded = {k: v for k, v in record.items() if k in allowed_keys}
    try:
        from autoskillit.hooks._join_ledger import write_diagnostic

        write_diagnostic(bounded, caller="declare_join_batch")
    except Exception as exc:
        print(
            f"declare_join_batch: diagnostic emission failed: {exc}", file=__import__("sys").stderr
        )


def _derive_artifact_digest(binding: dict[str, object]) -> str:
    """Reconstruct the artifact digest for the most recent loaded skill."""
    loaded = binding.get("loaded_skills", [])
    if isinstance(loaded, list) and loaded:
        last = loaded[-1]
        if isinstance(last, dict):
            candidate = last.get("artifact_digest")
            if isinstance(candidate, str) and candidate:
                return candidate
    return ""


@mcp.tool(
    tags={"autoskillit", "kitchen"},
    annotations={"readOnlyHint": False},
    meta={"anthropic/alwaysLoad": False},
)
@_cancellation_shield()
@track_response_size("declare_join_batch")
async def declare_join_batch(
    skill_name: str,
    assignments: list[str],
    session_id: str,
    top_level_parent: str | None = None,
) -> str:
    """Open one declared batch ledger for the next wave of direct children.

    Validates that the loaded skill, the session flag binding, and the
    artifact identity are all consistent. Returns the new ``join_batch_id``
    on success; a structured refusal on conflict.
    """
    try:
        result = _declare_join_batch_handler(
            skill_name=skill_name,
            assignments=assignments,
            session_id=session_id,
            top_level_parent=top_level_parent,
        )
    except Exception as exc:
        logger.error("declare_join_batch unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
    return json.dumps(result, sort_keys=True)


def _register_active_recipe_kitchen(ctx: ToolContext) -> None:
    """Publish one kitchen to both process and recipe-generation lifecycles."""
    from autoskillit.server._recipe_generation import activate_kitchen  # circular-break

    identity = cast(KitchenProcessIdentity, ctx.kitchen_process_identity)
    register_active_kitchen(identity)
    activate_kitchen(identity.kitchen_id)
