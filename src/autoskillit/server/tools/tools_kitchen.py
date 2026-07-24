"""MCP tool handlers and resource: open_kitchen, close_kitchen, recipe:// resource."""

from __future__ import annotations

import difflib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

if TYPE_CHECKING:
    from autoskillit.config.settings import OutputBudgetConfig, QuotaGuardConfig
    from autoskillit.pipeline import ToolContext

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

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
    CapabilityResolutionDetail,
    ProcessStaleError,
    RecipeDeliveryRequest,
    _collect_disabled_feature_tags,
    atomic_write,
    clear_kitchens_for_pid,
    fast_dumps,
    find_latest_session_id,
    get_logger,
    get_state_dir,
    is_marker_fresh,
    kitchen_entry_alive,
    read_active_kitchens_registry,
    read_marker,
    register_active_kitchen,
    resolve_kitchen_id,
    sweep_stale_markers,
    unregister_active_kitchen,
)
from autoskillit.fleet import (
    FleetSemaphore,
    discover_campaign_state_files,
    reap_stale_dispatches_async,
)
from autoskillit.pipeline import create_background_task
from autoskillit.server import mcp
from autoskillit.server._guards import _backend_supports_quota, _require_orchestrator_exact
from autoskillit.server._misc import (
    _apply_triage_gate,
    _build_hook_diagnostic_warning,
    _hook_config_overlay_path,
    _hook_config_path,
    _pipeline_tracker_dir,
    _pipeline_tracker_path,
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
    retire_recipe_artifacts,
)
from autoskillit.server._recipe_execution import clear_recipe_execution
from autoskillit.server.tools._authority_feedback import (
    build_authority_clobber_warnings,
    build_authority_rejection_envelope,
)
from autoskillit.server.tools._auto_overrides import (
    _compute_effective_backend_map,
    _promote_capability_keys,
    _provider_aware_capability_overrides,
)
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._pipeline_deps import _derive_phase_a_deps
from autoskillit.server.tools._preflight import (
    _check_dispatch_feasibility,
    filter_steps_by_post_prune,
)
from autoskillit.server.tools._serve_helpers import (
    build_backend_capabilities_map,
    build_open_kitchen_recipe_payload,
    pop_compiled_bindings,
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
    return json.dumps(
        {
            "success": False,
            "kitchen": "failed",
            "user_visible_message": msg,
            "error": f"{type(exc).__name__}: {exc}",
            "stage": stage,
        }
    )


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


async def _dispatch_infeasible_response(
    result: dict[str, Any],
    backend: Any,
    gate: Any,
    ctx: Context,
    capability_detail: CapabilityResolutionDetail | None = None,
) -> str:
    """Refuse a dead-on-arrival pipeline before the gate is enabled.

    Used by the open_kitchen (both normal and deferred-recall) paths when
    load_and_validate reports dispatch_feasible=False. When ``capability_detail``
    is provided and indicates a none_pass, the response carries
    ``missing_provider_steps`` and an ``escape_hatch`` key with actionable
    guidance, instead of blaming the backend generically.
    """
    gate.disable()
    await ctx.disable_components(tags={"kitchen"})
    _infeasible = result.get("infeasible_steps", [])
    _backend_name = backend.name if backend is not None else "unknown"
    _envelope: dict[str, Any] = {
        "success": False,
        "kitchen": "dispatch_infeasible",
        "infeasible_steps": _infeasible,
        "ingredients_table": result.get("ingredients_table"),
        "user_visible_message": (
            f"Cannot dispatch recipe: backend {_backend_name!r} "
            f"causes steps {_infeasible} to route to terminal failure. "
            f"Use a backend with git_metadata_writable=True (e.g. claude-code)."
        ),
    }
    if capability_detail is not None and capability_detail.resolution_path == "none_pass":
        _missing = list(capability_detail.missing_provider_steps)
        _envelope["missing_provider_steps"] = _missing
        _envelope["escape_hatch"] = (
            f"Add provider overrides with ANTHROPIC_BASE_URL for steps: "
            f"{_missing}. Example config: "
            f"providers.recipe_overrides.<recipe>.*: <profile>"
        )
        _envelope["user_visible_message"] = (
            f"Cannot dispatch recipe: backend {_backend_name!r} "
            f"lacks provider overrides for {_missing}. "
            f"{_envelope['escape_hatch']}"
        )
    return json.dumps(_envelope)


class QuotaGuardHookPayload(TypedDict):
    cache_max_age: int
    cache_path: str
    buffer_seconds: int
    disabled: bool


class OutputBudgetPolicyHookPayload(TypedDict):
    disabled: bool
    shell_max_inline_bytes: int


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
    return {
        "disabled": not cfg.guard_enabled,
        "shell_max_inline_bytes": cfg.shell_max_inline_bytes,
    }


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
    overlay_path = _hook_config_overlay_path(ctx.project_dir)
    try:
        payload = json.loads(hook_cfg_path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("hook_config_git_ops_policy_update_read_failed", path=str(hook_cfg_path))
        return
    git_ops_policy: dict = payload.get("git_ops_policy", {})
    if overlay_path.exists():
        try:
            overlay = json.loads(overlay_path.read_text())
            overlay_policy = overlay.get("git_ops_policy", {})
            if overlay_policy:
                git_ops_policy = {**git_ops_policy, **overlay_policy}
        except (OSError, json.JSONDecodeError):
            pass
    payload["git_ops_policy"] = git_ops_policy
    try:
        atomic_write(hook_cfg_path, json.dumps(payload))
    except OSError:
        logger.warning("hook_config_git_ops_policy_update_write_failed", path=str(hook_cfg_path))


_ORPHAN_GRACE_SECONDS = 600


def prune_stale_kitchen_state(project_dir: Path, current_kitchen_id: str) -> None:
    """Remove tracker files belonging to dead kitchens.

    Per tracker file in ``pipeline_tracker/``: parse → read internal
    ``kitchen_id`` (never the filename); if it equals *current_kitchen_id*
    → keep; else check **all** ``active_kitchens.json`` entries sharing that
    ``kitchen_id`` — keep the tracker iff any matching entry is alive. Reap
    only when all matching entries are dead. No matching entry at all → reap
    only if ``initialized_at`` exceeds the grace window.
    """
    logger = get_logger(__name__)
    tracker_dir = _pipeline_tracker_dir(project_dir)
    if not tracker_dir.is_dir():
        return

    try:
        entries = read_active_kitchens_registry()
    except Exception:
        logger.warning("prune_kitchen_state_registry_read_failed", exc_info=True)
        return

    for tracker_file in list(tracker_dir.glob("*.json")):
        if tracker_file.name.startswith("."):
            continue
        try:
            tracker_data = json.loads(tracker_file.read_text())
        except (json.JSONDecodeError, OSError):
            try:
                tracker_file.unlink()
            except OSError:
                pass
            continue

        tracker_kid = tracker_data.get("kitchen_id", "")
        if tracker_kid == current_kitchen_id:
            continue

        matching = [e for e in entries if e.get("kitchen_id") == tracker_kid]
        if matching:
            if any(kitchen_entry_alive(e) for e in matching):
                continue
            try:
                tracker_file.unlink()
            except OSError:
                pass
        else:
            init_at_str = tracker_data.get("initialized_at", "")
            try:
                init_at = datetime.fromisoformat(init_at_str)
                age = (datetime.now(UTC) - init_at).total_seconds()
            except (ValueError, TypeError):
                age = float("inf")
            if age > _ORPHAN_GRACE_SECONDS:
                try:
                    tracker_file.unlink()
                except OSError:
                    pass


def _auto_init_pipeline_tracker(tool_ctx: ToolContext) -> None:
    """Auto-derive and initialize the kitchen-scoped pipeline dependency tracker.

    Self-arming, server-internal counterpart to ``record_pipeline_step(op="init")``
    — runs at ``open_kitchen`` time from ``ctx.active_recipe_steps``, requiring
    no LLM action, mirroring how ingredient locks are primed. Writes directly
    via ``atomic_write`` rather than calling the MCP tool: ``record_pipeline_step``
    resolves its own pipeline_id from ``pipeline_id | AUTOSKILLIT_DISPATCH_ID``
    with no kitchen_id tier, and this stays independent of that resolution so
    fleet callers are unaffected.

    Idempotent across the deferred-override re-call pattern: an existing
    tracker's step statuses and previously-tracked dependency keys are
    preserved rather than overwritten.
    """
    active_steps = tool_ctx.active_recipe_steps
    if not active_steps:
        return
    try:
        deps = _derive_phase_a_deps(active_steps)
    except Exception:
        logger.warning("pipeline_tracker_auto_init_deps_failed", exc_info=True)
        return
    if not deps:
        return

    tracker_path = _pipeline_tracker_path(tool_ctx.project_dir, tool_ctx.kitchen_id)
    steps: dict[str, dict[str, str]] = {name: {"status": "pending"} for name in active_steps}
    dependencies: dict[str, list[str]] = dict(deps)

    if tracker_path.exists():
        try:
            existing = json.loads(tracker_path.read_text())
        except (OSError, json.JSONDecodeError):
            existing = {}
        for name, state in existing.get("steps", {}).items():
            if name in steps:
                steps[name] = state
        for key, value in existing.get("dependencies", {}).items():
            dependencies.setdefault(key, value)

    tracker_data = {
        "kitchen_id": tool_ctx.kitchen_id,
        "pipeline_id": tool_ctx.kitchen_id,
        "steps": steps,
        "dependencies": dependencies,
        "initialized_at": datetime.now(UTC).isoformat(),
    }
    try:
        atomic_write(tracker_path, fast_dumps(tracker_data))
    except OSError:
        logger.warning("pipeline_tracker_auto_init_write_failed", exc_info=True)


async def _open_kitchen_handler() -> str | None:
    """Set the tools-enabled flag. Extracted for testability.

    Returns ``None`` on success, or a JSON failure envelope string on error.
    """
    from autoskillit.server import _get_ctx, logger  # circular-break

    ctx = _get_ctx()
    ctx.gate.enable()
    ctx.kitchen_id = resolve_kitchen_id()
    ctx.active_recipe_packs = frozenset()
    ctx.active_recipe_features = frozenset()
    ctx.active_recipe_steps = {}
    ctx.active_recipe_ingredients = frozenset()
    clear_recipe_execution(ctx)
    logger.info("open_kitchen", gate_state="open", kitchen_id=ctx.kitchen_id)
    _supports_quota = _backend_supports_quota(ctx)

    try:
        _write_hook_config()
    except Exception as exc:
        ctx.gate.disable()
        logger.warning("open_kitchen_failure", stage="write_hook_config", exc_info=True)
        return _kitchen_failure_envelope(exc, stage="write_hook_config")

    try:
        await _prime_quota_cache(supports_quota_check=_supports_quota)
    except Exception as exc:
        ctx.gate.disable()
        logger.warning("open_kitchen_failure", stage="prime_quota_cache", exc_info=True)
        return _kitchen_failure_envelope(exc, stage="prime_quota_cache")

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
        logger.warning("open_kitchen_failure", stage="start_quota_refresh", exc_info=True)
        return _kitchen_failure_envelope(exc, stage="start_quota_refresh")

    try:
        clear_kitchens_for_pid(os.getpid())
    except Exception:
        logger.warning("open_kitchen_clear_pid_failed", exc_info=True)

    try:
        register_active_kitchen(ctx.kitchen_id, os.getpid(), str(ctx.project_dir))
    except Exception:
        logger.warning("open_kitchen_registry_failed", exc_info=True)

    try:
        prune_stale_kitchen_state(ctx.project_dir, ctx.kitchen_id)
    except Exception:
        logger.warning("open_kitchen_prune_trackers_failed", exc_info=True)

    try:
        sweep_stale_markers()
    except Exception:
        logger.warning("open_kitchen_sweep_markers_failed", exc_info=True)

    try:
        _campaign_state_paths = discover_campaign_state_files(ctx.project_dir)
        if _campaign_state_paths:
            await reap_stale_dispatches_async(
                _campaign_state_paths,
                min_reap_age_seconds=60.0,
                heartbeat_grace_seconds=90.0,
            )
    except Exception:
        logger.warning("open_kitchen_reap_failed", exc_info=True)

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
    # Pass 1: subset re-disable (existing)
    for subset in disabled:
        await ctx.disable_components(tags={subset})

    # Pass 2: feature gate — suppress tool tags for disabled features
    _features = features or {}
    for tag in _collect_disabled_feature_tags(
        _features, experimental_enabled=experimental_enabled
    ):
        await ctx.disable_components(tags={tag})


def _close_kitchen_handler() -> None:
    """Clear the tools-enabled flag. Extracted for testability."""
    from autoskillit.server import _get_ctx, logger  # circular-break

    ctx = _get_ctx()
    if ctx.quota_refresh_task is not None:
        ctx.quota_refresh_task.cancel()
        ctx.quota_refresh_task = None
    ctx.gate.disable()
    try:
        unregister_active_kitchen(ctx.kitchen_id)
    except Exception:
        logger.warning("close_kitchen_registry_failed", exc_info=True)
    if (
        isinstance(ctx.temp_dir, Path)
        and isinstance(ctx.kitchen_id, str)
        and ctx.kitchen_id
        and not retire_recipe_artifacts(ctx.temp_dir, kitchen_id=ctx.kitchen_id)
    ):
        logger.warning("close_kitchen_recipe_artifact_retirement_failed")
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
    hook_cfg_path = _hook_config_path(ctx.project_dir)
    try:
        hook_cfg_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("hook_config_remove_failed", path=str(hook_cfg_path))
    overlay_path = _hook_config_overlay_path(ctx.project_dir)
    try:
        overlay_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("hook_config_overlay_remove_failed", path=str(overlay_path))
    overlay_lock_path = overlay_path.with_suffix(".lock")
    try:
        overlay_lock_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("hook_config_overlay_lock_remove_failed", path=str(overlay_lock_path))
    ctx.fleet_lock = None
    try:
        ctx.fleet_lock = FleetSemaphore(
            max_concurrent=ctx.config.fleet.max_concurrent_dispatches,
            timeout=ctx.config.fleet.acquire_timeout_sec,
        )
    except (TypeError, ValueError):
        logger.warning("fleet_lock_reset_skipped", exc_info=True)
    tracker_dir = _pipeline_tracker_dir(ctx.project_dir)
    try:
        if tracker_dir.is_dir():
            import shutil

            shutil.rmtree(tracker_dir, ignore_errors=True)
    except OSError:
        logger.warning("pipeline_tracker_remove_failed", path=str(tracker_dir))
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


@mcp.resource("recipe://{name}")
def get_recipe(name: str) -> str:
    """Return composed recipe YAML for the orchestrating agent to follow."""
    from autoskillit.server._state import _get_ctx_or_none  # circular-break

    ctx = _get_ctx_or_none()
    if ctx is None or ctx.recipes is None:
        return json.dumps({"error": "Kitchen not open."})
    clear_recipe_execution(ctx)
    match = ctx.recipes.find(name, ctx.project_dir)
    if match is None:
        return json.dumps({"error": f"No recipe named '{name}'."})

    _defaults = resolve_ingredient_defaults(ctx.project_dir)
    _config_layer = build_config_authoritative_layer(_defaults)
    _session_overrides: dict[str, str] = {
        "kitchen_id": ctx.kitchen_id,
        "diagnostics_log_dir": str(resolve_log_dir(ctx.config.linux_tracing.log_dir)),
    }
    try:
        _raw_recipe = ctx.recipes.load(match.path)
        _backend_overrides, _cap_detail = _provider_aware_capability_overrides(
            ctx.backend,
            name,
            ctx.config.providers,
            _raw_recipe.steps,
            skill_resolver=ctx.skill_resolver,
            config_backend=ctx.config.agent_backend,
            project_root=ctx.project_dir,
        )
        _effective_backend_map, _backend_origin_map = _compute_effective_backend_map(
            _raw_recipe.steps,
            ctx.backend.name if ctx.backend else None,
            ctx.config.providers,
            name,
            skill_resolver=ctx.skill_resolver,
            config_backend=ctx.config.agent_backend,
            project_root=ctx.project_dir,
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
            backend_overrides=_backend_overrides,
            resolved_defaults=_defaults,
            effective_backend_map=_effective_backend_map,
            backend_name=ctx.backend.name if ctx.backend else None,
            backend_capabilities_map=_backend_capabilities_map,
            backend_origin_map=_backend_origin_map,
        )
        _resource_compiled_bindings = pop_compiled_bindings(result)
    except ProcessStaleError:
        logger.warning("get_recipe_failure", recipe=name, stage="process_stale", exc_info=True)
        return json.dumps({"error": f"Recipe '{name}' composition failed — process stale."})
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
    if not result.get("dispatch_feasible", True):
        return json.dumps(
            {
                "error": "Recipe is infeasible on current backend",
                "dispatch_feasible": False,
                "infeasible_steps": result.get("infeasible_steps", []),
            }
        )
    finalized = finalize_recipe_delivery(
        result,
        surface="get_recipe",
        recipe_name=name,
        tool_ctx=ctx,
        compiled_bindings=_resource_compiled_bindings,
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


@mcp.tool(
    tags={"autoskillit"},
    annotations={"readOnlyHint": True},
    meta=response_backstop_tool_meta("open_kitchen", always_load=True),
)
@document_recipe_delivery_contract
@_cancellation_shield()
@track_response_size("open_kitchen")
async def open_kitchen(
    name: str | None = None,
    overrides: dict[str, str] | None = None,
    ingredients_only: bool = False,
    delivery_request: RecipeDeliveryRequest | None = None,
    ctx: Context = CurrentContext(),
) -> str:
    """Open the AutoSkillit kitchen for service.

    When ``name`` is provided, the kitchen is opened AND the named recipe is
    loaded in a single call, reducing terminal noise from two tool calls to one.

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

        disabled_subsets = _get_ctx().config.subsets.disabled

        _ctx_pre = _get_ctx()
        _skip_handler = _ctx_pre.gate_infrastructure_ready
        tool_ctx = _get_ctx()

        if not _skip_handler:
            handler_err = await _open_kitchen_handler()
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
            _kctx_pre = _get_ctx()
            _skip_notify = (
                _kctx_pre.backend is not None
                and not _kctx_pre.backend.capabilities.supports_tool_list_changed
            )
            if _skip_notify:
                logger.debug("open_kitchen_skip_enable", reason="pre-revealed at startup")
            else:
                try:
                    await ctx.enable_components(tags={"kitchen"})
                except Exception as exc:
                    logger.warning(
                        "open_kitchen_failure", stage="enable_components", exc_info=True
                    )
                    tool_ctx.gate_infrastructure_ready = False
                    return _kitchen_failure_envelope(exc, stage="enable_components")

            try:
                _kctx = _get_ctx()
                await _redisable_subsets(
                    ctx,
                    disabled_subsets,
                    _kctx.config.features,
                    experimental_enabled=_kctx.config.experimental_enabled,
                )
            except Exception as exc:
                logger.warning("open_kitchen_failure", stage="redisable_subsets", exc_info=True)
                tool_ctx.gate_infrastructure_ready = False
                return _kitchen_failure_envelope(exc, stage="redisable_subsets")

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
            try:
                _recipe_info = tool_ctx.recipes.find(name, tool_ctx.project_dir)
            except Exception:
                logger.warning("open_kitchen_early_find_failed", recipe=name, exc_info=True)
                _recipe_info = None
            _raw_recipe = (
                tool_ctx.recipes.load(_recipe_info.path) if _recipe_info is not None else None
            )
            _session_overrides: dict[str, str] = {
                "kitchen_id": tool_ctx.kitchen_id,
                "diagnostics_log_dir": str(resolve_log_dir(tool_ctx.config.linux_tracing.log_dir)),
            }
            _provider_overrides, _cap_detail = _provider_aware_capability_overrides(
                tool_ctx.backend,
                name,
                tool_ctx.config.providers,
                _raw_recipe.steps if _raw_recipe is not None else None,
                skill_resolver=tool_ctx.skill_resolver,
                config_backend=tool_ctx.config.agent_backend,
                project_root=tool_ctx.project_dir,
            )
            _session_overrides.update(_provider_overrides)
            _config_layer = build_config_authoritative_layer(_defaults)
            _config_default = build_config_default_layer(_defaults)
            _promote_capability_keys(_config_layer, _session_overrides)
            _effective_backend_map, _backend_origin_map = _compute_effective_backend_map(
                _raw_recipe.steps if _raw_recipe is not None else None,
                tool_ctx.backend.name if tool_ctx.backend else None,
                tool_ctx.config.providers,
                name,
                skill_resolver=tool_ctx.skill_resolver,
                config_backend=tool_ctx.config.agent_backend,
                project_root=tool_ctx.project_dir,
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
                    _deferred_compiled_bindings = pop_compiled_bindings(result)
                except ProcessStaleError as exc:
                    logger.warning("open_kitchen_failure", stage="process_stale", exc_info=True)
                    return _kitchen_failure_envelope(exc, stage="process_stale")
                except Exception as exc:
                    logger.warning(
                        "open_kitchen_failure", stage="load_and_validate", exc_info=True
                    )
                    return _kitchen_failure_envelope(exc, stage="load_and_validate")
                tool_ctx.active_recipe_packs = frozenset(result.get("requires_packs", []))
                tool_ctx.active_recipe_features = frozenset(result.get("requires_features", []))
                tool_ctx.recipe_content_hash = result.get("content_hash", "")
                tool_ctx.recipe_composite_hash = result.get("composite_hash", "")
                tool_ctx.recipe_version = result.get("recipe_version") or ""
                recipe_info = None
                _deferred_recipe_obj = None
                try:
                    recipe_info = tool_ctx.recipes.find(name, tool_ctx.project_dir)
                except Exception:
                    logger.warning("open_kitchen_failure", stage="recipe_find", exc_info=True)
                    tool_ctx.active_recipe_steps = None
                    tool_ctx.active_recipe_ingredients = None
                else:
                    if recipe_info is not None:
                        try:
                            recipe_obj = tool_ctx.recipes.load(recipe_info.path)
                            _deferred_recipe_obj = recipe_obj
                            tool_ctx.active_recipe_steps = filter_steps_by_post_prune(
                                recipe_obj.steps, result.get("post_prune_step_names", [])
                            )
                            tool_ctx.active_recipe_ingredients = frozenset(
                                recipe_obj.ingredients.keys()
                            )
                        except Exception:
                            logger.warning("open_kitchen_recipe_steps_cache_failed", exc_info=True)
                            tool_ctx.active_recipe_steps = None
                            tool_ctx.active_recipe_ingredients = None
                    else:
                        tool_ctx.active_recipe_steps = None
                        tool_ctx.active_recipe_ingredients = None
                # Default to False for missing 'valid' so a absent key is treated as invalid
                if not result.get("valid", False) or not result.get("content", ""):
                    tool_ctx.gate.disable()
                    tool_ctx.gate_infrastructure_ready = False
                    return _recipe_validation_error_response(name, result)
                if not result.get("dispatch_feasible", True):
                    return await _dispatch_infeasible_response(
                        result,
                        tool_ctx.backend,
                        tool_ctx.gate,
                        ctx,
                        capability_detail=_cap_detail,
                    )
                # Dispatch-feasibility preflight: verify the backend can enforce
                # all fix-required hooks for the recipe's run_skill steps.
                if tool_ctx.active_recipe_steps is not None:
                    _auto_init_pipeline_tracker(tool_ctx)
                    _preflight_err = _check_dispatch_feasibility(
                        post_prune_step_names=result.get("post_prune_step_names", []),
                        active_recipe_steps=tool_ctx.active_recipe_steps,
                        backend=tool_ctx.backend,
                        config_providers=tool_ctx.config.providers,
                        recipe_name=name,
                        config_backend=tool_ctx.config.agent_backend,
                        skill_resolver=tool_ctx.skill_resolver,
                        project_root=tool_ctx.project_dir,
                    )
                    if _preflight_err is not None:
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
                    return cast(
                        str,
                        finalize_recipe_delivery(
                            result,
                            surface="open_kitchen_deferred_recall",
                            recipe_name=name,
                            tool_ctx=tool_ctx,
                            delivery_request=delivery_request,
                            compiled_bindings=_deferred_compiled_bindings,
                        ),
                    )
                return render_served_response(result)
            try:
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
                _normal_compiled_bindings = pop_compiled_bindings(result)
            except ProcessStaleError as exc:
                logger.warning("open_kitchen_failure", stage="process_stale", exc_info=True)
                return _kitchen_failure_envelope(exc, stage="process_stale")
            except Exception as exc:
                logger.warning("open_kitchen_failure", stage="load_and_validate", exc_info=True)
                return _kitchen_failure_envelope(exc, stage="load_and_validate")

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

            try:
                recipe_info = tool_ctx.recipes.find(name, tool_ctx.project_dir)
            except Exception as exc:
                logger.warning("open_kitchen_failure", stage="recipe_find", exc_info=True)
                return _kitchen_failure_envelope(exc, stage="recipe_find")

            _normal_recipe_obj = None
            if recipe_info is not None:
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
            else:
                tool_ctx.active_recipe_steps = None
                tool_ctx.active_recipe_ingredients = None

            try:
                result = await _apply_triage_gate(result, name, recipe_info=recipe_info)
            except Exception as exc:
                logger.warning("open_kitchen_failure", stage="apply_triage_gate", exc_info=True)
                return _kitchen_failure_envelope(exc, stage="apply_triage_gate")

            if not result.get("valid", False) or not result.get("content", ""):
                tool_ctx.gate.disable()
                tool_ctx.gate_infrastructure_ready = False
                return _recipe_validation_error_response(name, result)

            if not result.get("dispatch_feasible", True):
                return await _dispatch_infeasible_response(
                    result,
                    tool_ctx.backend,
                    tool_ctx.gate,
                    ctx,
                    capability_detail=_cap_detail,
                )

            # Dispatch-feasibility preflight: verify the backend can enforce
            # all fix-required hooks for the recipe's run_skill steps.
            if tool_ctx.active_recipe_steps is not None:
                try:
                    prune_stale_kitchen_state(tool_ctx.project_dir, tool_ctx.kitchen_id)
                except Exception:
                    logger.warning("open_kitchen_deferred_prune_failed", exc_info=True)
                _auto_init_pipeline_tracker(tool_ctx)
                _preflight_err = _check_dispatch_feasibility(
                    post_prune_step_names=result.get("post_prune_step_names", []),
                    active_recipe_steps=tool_ctx.active_recipe_steps,
                    backend=tool_ctx.backend,
                    config_providers=tool_ctx.config.providers,
                    recipe_name=name,
                    config_backend=tool_ctx.config.agent_backend,
                    skill_resolver=tool_ctx.skill_resolver,
                    project_root=tool_ctx.project_dir,
                )
                if _preflight_err is not None:
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
                warning = _build_hook_diagnostic_warning()
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
                return cast(
                    str,
                    finalize_recipe_delivery(
                        result,
                        surface="open_kitchen",
                        recipe_name=name,
                        tool_ctx=tool_ctx,
                        delivery_request=delivery_request,
                        compiled_bindings=_normal_compiled_bindings,
                    ),
                )

            return render_served_response(result)

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
            warning = _build_hook_diagnostic_warning()
        except Exception as exc:
            logger.warning("open_kitchen_failure", stage="hook_diagnostic", exc_info=True)
            return _kitchen_failure_envelope(exc, stage="hook_diagnostic")
        if warning:
            text += warning

        return render_served_response(
            {
                "success": True,
                "kitchen": "open",
                "content": text,
                "ingredients_table": None,
                "version": __version__,
            }
        )
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

        mcp.disable(tags={"kitchen"})
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
) -> tuple[bool, dict]:
    """Atomically read-modify-write ingredient locks under flock.

    All state reads and merges happen inside the flock to prevent concurrent
    callers from overwriting each other's updates.
    """
    overlay_path = _hook_config_overlay_path(project_dir)
    lock_path = overlay_path.with_suffix(".lock")
    overlay_path.parent.mkdir(parents=True, exist_ok=True)

    import fcntl

    with open(lock_path, "wb") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            existing = {}
            if overlay_path.exists():
                try:
                    existing = json.loads(overlay_path.read_text())
                except (json.JSONDecodeError, OSError):
                    pass

            li = existing.setdefault("locked_ingredients", {})
            current_pipeline_li = dict(li.get(pipeline_id, {}))

            if unlock_keys:
                _apply_unlock_keys(current_pipeline_li, unlock_keys)

            if new_locked:
                current_pipeline_li.update(new_locked)

            if new_locked or unlock_keys:
                li[pipeline_id] = current_pipeline_li

            unlocked_steps = _compute_unlocked_steps(active_steps, current_pipeline_li)
            ls = existing.setdefault("locked_steps", {})
            if new_locked or unlock_keys:
                ls[pipeline_id] = unlocked_steps

            atomic_write(overlay_path, json.dumps(existing))
            return True, existing
        finally:
            pass


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

        success, updated = _write_ingredient_locks(
            ctx.project_dir,
            effective_pipeline_id,
            locked,
            unlock,
            active_steps,
        )

        return json.dumps(
            {
                "success": success,
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
