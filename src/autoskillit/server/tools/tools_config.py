"""Session-scoped configuration MCP tools: configure_fleet, configure_order."""

from __future__ import annotations

import json
from dataclasses import fields as dc_fields

from autoskillit.core import FleetLock, atomic_write, get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import _require_orchestrator_exact
from autoskillit.server._misc import _hook_config_overlay_path, _hook_config_path
from autoskillit.server._notify import track_response_size
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

logger = get_logger(__name__)


def _write_session_config(
    project_dir, domain: str, params: dict, core_params: dict | None = None
) -> tuple[bool, dict | str]:
    """Read-merge-write domain params into the overlay file.

    Returns (success, overlay_dict_or_error_message).
    """
    if not _hook_config_path(project_dir).exists():
        return (False, "Kitchen is not open — hook config file absent.")

    overlay_path = _hook_config_overlay_path(project_dir)
    overlay_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if overlay_path.exists():
            existing = json.loads(overlay_path.read_text())
        else:
            existing = {}
    except (json.JSONDecodeError, OSError):
        existing = {}

    domain_data = dict(existing.get(domain, {}))
    domain_data.update(params)
    existing[domain] = domain_data

    if core_params:
        core_data = dict(existing.get("core", {}))
        core_data.update(core_params)
        existing["core"] = core_data

    atomic_write(overlay_path, json.dumps(existing))
    return (True, existing)


def _build_config_snapshot(
    config, domain: str, overlay: dict, *, fleet_lock: FleetLock | None = None
) -> dict:
    """Build complete config snapshot: dataclass defaults + overlay overrides."""
    if domain == "fleet":
        defaults = {f.name: getattr(config.fleet, f.name) for f in dc_fields(config.fleet)}
    elif domain == "order":
        defaults = {f.name: getattr(config.run_skill, f.name) for f in dc_fields(config.run_skill)}
    else:
        defaults = {}

    core_defaults = {f.name: getattr(config.model, f.name) for f in dc_fields(config.model)}
    core_overrides = overlay.get("core", {})

    domain_overrides = overlay.get(domain, {})
    snapshot_domain = {**defaults, **domain_overrides}

    if domain == "fleet" and fleet_lock is not None:
        snapshot_domain["max_concurrent_dispatches"] = fleet_lock.max_concurrent
        if fleet_lock.timeout is not None:
            snapshot_domain["acquire_timeout_sec"] = fleet_lock.timeout

    return {domain: snapshot_domain, "core": {**core_defaults, **core_overrides}}


def _collect_fleet_params(
    max_concurrent_dispatches: int | None,
    max_total_issues: int | None,
    default_timeout_sec: int | None,
    max_extension_seconds: float | None,
    idle_output_timeout: float | None,
    acquire_timeout_sec: float | None,
    max_issues_per_food_truck: int | None,
    enable_deadline_extension: bool | None,
) -> dict:
    params: dict = {}
    for name, val in [
        ("max_concurrent_dispatches", max_concurrent_dispatches),
        ("max_total_issues", max_total_issues),
        ("default_timeout_sec", default_timeout_sec),
        ("max_extension_seconds", max_extension_seconds),
        ("idle_output_timeout", idle_output_timeout),
        ("acquire_timeout_sec", acquire_timeout_sec),
        ("max_issues_per_food_truck", max_issues_per_food_truck),
        ("enable_deadline_extension", enable_deadline_extension),
    ]:
        if val is not None:
            params[name] = val
    return params


def _collect_order_params(
    timeout: int | None,
    stale_threshold: int | None,
    idle_output_timeout: int | None,
    max_suppression_seconds: int | None,
) -> dict:
    params: dict = {}
    for name, val in [
        ("timeout", timeout),
        ("stale_threshold", stale_threshold),
        ("idle_output_timeout", idle_output_timeout),
        ("max_suppression_seconds", max_suppression_seconds),
    ]:
        if val is not None:
            params[name] = val
    return params


def _validate_max_concurrent(value: int) -> str | None:
    from autoskillit.config import _MAX_CONCURRENT_DISPATCHES

    if value < 1 or value > _MAX_CONCURRENT_DISPATCHES:
        return (
            f"max_concurrent_dispatches must be between 1 and "
            f"{_MAX_CONCURRENT_DISPATCHES}, got {value}"
        )
    return None


def _replace_fleet_semaphore(ctx, max_concurrent: int, acquire_timeout_sec: float | None) -> None:
    from autoskillit.fleet import FleetSemaphore

    existing_timeout = ctx.fleet_lock.timeout if ctx.fleet_lock is not None else None
    timeout = acquire_timeout_sec if acquire_timeout_sec is not None else existing_timeout
    ctx.fleet_lock = FleetSemaphore(max_concurrent=max_concurrent, timeout=timeout)


@mcp.tool(
    tags={"autoskillit"}, annotations={"readOnlyHint": True}, meta={"anthropic/alwaysLoad": True}
)
@_cancellation_shield()
@track_response_size("configure_fleet")
async def configure_fleet(
    max_concurrent_dispatches: int | None = None,
    max_total_issues: int | None = None,
    default_timeout_sec: int | None = None,
    max_extension_seconds: float | None = None,
    idle_output_timeout: float | None = None,
    acquire_timeout_sec: float | None = None,
    max_issues_per_food_truck: int | None = None,
    enable_deadline_extension: bool | None = None,
    default_model: str | None = None,
) -> str:
    """Configure fleet dispatch parameters for this kitchen session.

    Sets fleet-specific and core parameters that persist for the remainder
    of this kitchen session. Call after open_kitchen. Parameters not
    explicitly set retain their defaults from FleetConfig.

    Returns a complete config snapshot (all fleet + core values, including
    defaults for anything not explicitly set). Use this snapshot as the
    single source of truth for session limits.

    Session-scoped only: configuration is cleared when the kitchen is
    closed and reopened. Does not modify persistent configuration.

    Never raises.
    """
    try:
        if (h := _require_orchestrator_exact("configure_fleet")) is not None:
            return h
        from autoskillit.server import _get_ctx

        ctx = _get_ctx()

        if max_concurrent_dispatches is not None:
            if (err := _validate_max_concurrent(max_concurrent_dispatches)) is not None:
                return json.dumps({"success": False, "error": err})

        fleet_params = _collect_fleet_params(
            max_concurrent_dispatches,
            max_total_issues,
            default_timeout_sec,
            max_extension_seconds,
            idle_output_timeout,
            acquire_timeout_sec,
            max_issues_per_food_truck,
            enable_deadline_extension,
        )

        core_params = {"default_model": default_model} if default_model is not None else None

        ok, result = _write_session_config(ctx.project_dir, "fleet", fleet_params, core_params)
        if not ok:
            return json.dumps({"success": False, "error": result})

        if max_concurrent_dispatches is not None or acquire_timeout_sec is not None:
            effective_max = (
                max_concurrent_dispatches
                if max_concurrent_dispatches is not None
                else (
                    ctx.fleet_lock.max_concurrent
                    if ctx.fleet_lock is not None
                    else ctx.config.fleet.max_concurrent_dispatches
                )
            )
            _pre_timeout = ctx.fleet_lock.timeout if ctx.fleet_lock is not None else None
            _replace_fleet_semaphore(ctx, effective_max, acquire_timeout_sec)
            if ctx.fleet_lock is not None:
                if max_concurrent_dispatches is not None:
                    if ctx.fleet_lock.max_concurrent != max_concurrent_dispatches:
                        raise RuntimeError(
                            f"fleet_lock.max_concurrent {ctx.fleet_lock.max_concurrent!r} "
                            f"!= requested {max_concurrent_dispatches!r}"
                        )
                if acquire_timeout_sec is not None:
                    if ctx.fleet_lock.timeout != acquire_timeout_sec:
                        raise RuntimeError(
                            f"fleet_lock.timeout {ctx.fleet_lock.timeout!r} "
                            f"!= requested {acquire_timeout_sec!r}"
                        )
                else:
                    if ctx.fleet_lock.timeout != _pre_timeout:
                        raise RuntimeError(
                            f"fleet_lock.timeout {ctx.fleet_lock.timeout!r} "
                            f"!= pre-replace value {_pre_timeout!r}"
                        )

        assert isinstance(result, dict)
        snapshot = _build_config_snapshot(ctx.config, "fleet", result, fleet_lock=ctx.fleet_lock)
        return json.dumps({"success": True, "config": snapshot})
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
    """Configure order/orchestrator parameters for this kitchen session.

    Sets order-specific (RunSkillConfig) and core parameters that persist
    for the remainder of this kitchen session. Call after open_kitchen.
    Parameters not explicitly set retain their defaults from RunSkillConfig.

    Returns a complete config snapshot (all order + core values, including
    defaults for anything not explicitly set). Use this snapshot as the
    single source of truth for session limits.

    Session-scoped only: configuration is cleared when the kitchen is
    closed and reopened. Does not modify persistent configuration.

    Never raises.
    """
    try:
        if (h := _require_orchestrator_exact("configure_order")) is not None:
            return h
        from autoskillit.server import _get_ctx

        ctx = _get_ctx()

        order_params = _collect_order_params(
            timeout, stale_threshold, idle_output_timeout, max_suppression_seconds
        )

        core_params = {"default_model": default_model} if default_model is not None else None

        ok, result = _write_session_config(ctx.project_dir, "order", order_params, core_params)
        if not ok:
            return json.dumps({"success": False, "error": result})

        assert isinstance(result, dict)
        snapshot = _build_config_snapshot(ctx.config, "order", result)
        return json.dumps({"success": True, "config": snapshot})
    except Exception as exc:
        logger.error("configure_order unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
