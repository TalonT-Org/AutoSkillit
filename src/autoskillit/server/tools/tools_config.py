"""Session-scoped configuration MCP tools: configure_fleet, configure_order."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import fields as dc_fields
from dataclasses import replace
from typing import TYPE_CHECKING

from autoskillit.core import get_logger
from autoskillit.fleet import FleetSemaphore
from autoskillit.server import mcp
from autoskillit.server._guards import _require_orchestrator_exact
from autoskillit.server._misc import _hook_config_path
from autoskillit.server._notify import track_response_size
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._overlay_state import (
    OverlayStateError,
    locked_overlay,
    validate_overlay_document,
    write_overlay_locked,
)

if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig
    from autoskillit.core import FleetLock
    from autoskillit.pipeline import ToolContext

logger = get_logger(__name__)


def _build_config_snapshot(
    config: AutomationConfig,
    domain: str,
    *,
    fleet_lock: FleetLock | None = None,
) -> dict[str, dict[str, object]]:
    """Serialize a snapshot from the effective runtime configuration."""
    section = config.fleet if domain == "fleet" else config.run_skill
    snapshot_domain = {item.name: getattr(section, item.name) for item in dc_fields(section)}
    if domain == "fleet" and fleet_lock is not None:
        snapshot_domain["max_concurrent_dispatches"] = fleet_lock.max_concurrent
        snapshot_domain["acquire_timeout_sec"] = fleet_lock.timeout
    return {
        domain: snapshot_domain,
        "core": {item.name: getattr(config.model, item.name) for item in dc_fields(config.model)},
    }


def _collect_fleet_params(
    max_concurrent_dispatches: int | None,
    default_timeout_sec: int | None,
    max_extension_seconds: float | None,
    idle_output_timeout: float | None,
    acquire_timeout_sec: float | None,
    enable_deadline_extension: bool | None,
    inspector_model: str | None = None,
) -> dict[str, object]:
    return {
        name: value
        for name, value in (
            ("max_concurrent_dispatches", max_concurrent_dispatches),
            ("default_timeout_sec", default_timeout_sec),
            ("max_extension_seconds", max_extension_seconds),
            ("idle_output_timeout", idle_output_timeout),
            ("acquire_timeout_sec", acquire_timeout_sec),
            ("enable_deadline_extension", enable_deadline_extension),
            ("inspector_model", inspector_model),
        )
        if value is not None
    }


def _collect_order_params(
    timeout: int | None,
    stale_threshold: int | None,
    idle_output_timeout: int | None,
    max_suppression_seconds: int | None,
) -> dict[str, object]:
    return {
        name: value
        for name, value in (
            ("timeout", timeout),
            ("stale_threshold", stale_threshold),
            ("idle_output_timeout", idle_output_timeout),
            ("max_suppression_seconds", max_suppression_seconds),
        )
        if value is not None
    }


def _stage_effective_config(
    ctx: ToolContext,
    domain: str,
    params: dict[str, object],
    core_params: dict[str, object],
) -> tuple[dict[str, dict[str, object]], AutomationConfig, FleetLock | None]:
    staged_overrides = deepcopy(ctx._session_config_overrides)
    staged_overrides.setdefault(domain, {}).update(params)
    staged_overrides.setdefault("core", {}).update(core_params)
    validate_overlay_document(staged_overrides)

    baseline = deepcopy(ctx._baseline_config)
    candidate = replace(
        baseline,
        run_skill=replace(baseline.run_skill, **staged_overrides.get("order", {})),
        fleet=replace(baseline.fleet, **staged_overrides.get("fleet", {})),
        model=replace(baseline.model, **staged_overrides.get("core", {})),
    )
    candidate.fleet.validate(feature_enabled=True)

    candidate_lock: FleetLock | None = None
    if (
        domain == "fleet"
        and {
            "max_concurrent_dispatches",
            "acquire_timeout_sec",
        }
        & params.keys()
    ):
        candidate_lock = FleetSemaphore(
            max_concurrent=candidate.fleet.max_concurrent_dispatches,
            timeout=candidate.fleet.acquire_timeout_sec,
        )
    return staged_overrides, candidate, candidate_lock


def _commit_effective_config(
    ctx: ToolContext,
    domain: str,
    params: dict[str, object],
    core_params: dict[str, object],
) -> dict[str, dict[str, object]]:
    with locked_overlay(ctx.project_dir) as (overlay_path, overlay):
        if not ctx.gate.enabled or not _hook_config_path(ctx.project_dir).exists():
            raise OverlayStateError("Kitchen is not open — hook config file absent.")
        staged_overrides, candidate, candidate_lock = _stage_effective_config(
            ctx,
            domain,
            params,
            core_params,
        )
        overlay.setdefault(domain, {}).update(params)
        if core_params:
            overlay.setdefault("core", {}).update(core_params)
        write_overlay_locked(overlay_path, overlay)

        # Explicit call/step values win over these context-local session overrides;
        # the persisted overlay is synchronized persistence, not a runtime read source.
        ctx._session_config_overrides = staged_overrides
        ctx.config = candidate
        if candidate_lock is not None:
            ctx.fleet_lock = candidate_lock

    return _build_config_snapshot(ctx.config, domain, fleet_lock=ctx.fleet_lock)


def _configuration_error(exc: Exception) -> str:
    return json.dumps({"success": False, "error": f"Invalid session configuration: {exc}"})


@mcp.tool(
    tags={"autoskillit"}, annotations={"readOnlyHint": True}, meta={"anthropic/alwaysLoad": True}
)
@_cancellation_shield()
@track_response_size("configure_fleet")
async def configure_fleet(
    max_concurrent_dispatches: int | None = None,
    default_timeout_sec: int | None = None,
    max_extension_seconds: float | None = None,
    idle_output_timeout: float | None = None,
    acquire_timeout_sec: float | None = None,
    enable_deadline_extension: bool | None = None,
    inspector_model: str | None = None,
    default_model: str | None = None,
) -> str:
    """Configure values observed by the next fleet operation in this kitchen.

    Explicit dispatch/call values retain precedence over configured defaults. Both
    configuration tools update the same ``core.default_model`` override, so the last
    configuration call wins; explicit model values still take precedence. Fleet issue
    count limits remain static startup-prompt settings and are not dynamic parameters.

    The returned snapshot is serialized from the same effective configuration used by
    runtime consumers. Closing and reopening the kitchen restores construction defaults.
    Never raises.
    """
    try:
        if (guard := _require_orchestrator_exact("configure_fleet")) is not None:
            return guard
        from autoskillit.server import _get_ctx  # circular-break

        ctx = _get_ctx()
        params = _collect_fleet_params(
            max_concurrent_dispatches,
            default_timeout_sec,
            max_extension_seconds,
            idle_output_timeout,
            acquire_timeout_sec,
            enable_deadline_extension,
            inspector_model,
        )
        core_params: dict[str, object] = (
            {"default_model": default_model} if default_model is not None else {}
        )
        snapshot = _commit_effective_config(ctx, "fleet", params, core_params)
        return json.dumps({"success": True, "config": snapshot})
    except (OverlayStateError, TypeError, ValueError) as exc:
        return _configuration_error(exc)
    except Exception as exc:
        logger.error("configure_fleet unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(
    tags={"autoskillit"}, annotations={"readOnlyHint": True}, meta={"anthropic/alwaysLoad": True}
)
@_cancellation_shield()
@track_response_size("configure_order")
async def configure_order(
    timeout: int | None = None,
    stale_threshold: int | None = None,
    idle_output_timeout: int | None = None,
    max_suppression_seconds: int | None = None,
    default_model: str | None = None,
) -> str:
    """Configure values observed by the next order operation in this kitchen.

    Explicit step/call values retain precedence over configured defaults. Both
    configuration tools update the same ``core.default_model`` override, so the last
    configuration call wins; explicit model values still take precedence.

    The returned snapshot is serialized from the same effective configuration used by
    runtime consumers. Closing and reopening the kitchen restores construction defaults.
    Never raises.
    """
    try:
        if (guard := _require_orchestrator_exact("configure_order")) is not None:
            return guard
        from autoskillit.server import _get_ctx  # circular-break

        ctx = _get_ctx()
        params = _collect_order_params(
            timeout,
            stale_threshold,
            idle_output_timeout,
            max_suppression_seconds,
        )
        core_params: dict[str, object] = (
            {"default_model": default_model} if default_model is not None else {}
        )
        snapshot = _commit_effective_config(ctx, "order", params, core_params)
        return json.dumps({"success": True, "config": snapshot})
    except (OverlayStateError, TypeError, ValueError) as exc:
        return _configuration_error(exc)
    except Exception as exc:
        logger.error("configure_order unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
