"""Headless one-shot fleet dispatch command."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NoReturn

from cyclopts import Parameter

from autoskillit.core import get_logger, is_feature_enabled

if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig
    from autoskillit.core import CodingAgentBackend
    from autoskillit.fleet import DispatchResult

logger = get_logger(__name__)


def _fleet_run_error(
    error: str,
    message: str,
    exit_code: int = 1,
    *,
    dispatch_status: str | None = None,
) -> NoReturn:
    """Emit a CLI error envelope and exit.

    Every envelope carries a ``dispatch_status`` field so downstream consumers
    can distinguish crash outcomes from real dispatch outcomes without parsing
    the message string. When ``dispatch_status`` is omitted, the default
    ``"rejected"`` is used (no subprocess was launched).
    """
    envelope: dict[str, object] = {
        "success": False,
        "error": error,
        "user_visible_message": message,
        "dispatch_status": "rejected" if dispatch_status is None else dispatch_status,
    }
    print(json.dumps(envelope))
    raise SystemExit(exit_code)


async def _execute_fleet_run(
    cfg: AutomationConfig,
    recipe: str,
    task: str,
    ingredients: dict[str, str] | None,
    timeout_sec: int | None,
    dispatch_backend: CodingAgentBackend | None,
    resume_session_id: str | None,
    prior_dispatch_id: str | None,
    disable_quota_guard: bool,
) -> DispatchResult:
    import functools

    from autoskillit.core import detect_autoskillit_mcp_prefix
    from autoskillit.fleet import _build_food_truck_prompt, execute_dispatch
    from autoskillit.server import make_context

    ctx = make_context(cfg, project_dir=Path.cwd())

    from autoskillit.server import (  # noqa: PLC0415
        _compute_effective_backend_map,
        _provider_aware_capability_overrides,
    )

    _recipe_info = ctx.recipes.find(recipe, ctx.project_dir) if ctx.recipes else None
    _raw_steps = (
        ctx.recipes.load(_recipe_info.path).steps
        if _recipe_info is not None and ctx.recipes is not None
        else None
    )
    _prov_overrides, _ = _provider_aware_capability_overrides(
        dispatch_backend,
        recipe,
        ctx.config.providers,
        _raw_steps,
        skill_resolver=ctx.skill_resolver,
        config_backend=ctx.config.agent_backend,
    )
    _effective_backend_map = _compute_effective_backend_map(
        _raw_steps,
        dispatch_backend.name if dispatch_backend else None,
        ctx.config.providers,
        recipe,
        skill_resolver=ctx.skill_resolver,
        config_backend=ctx.config.agent_backend,
    )

    effective_backend = dispatch_backend or ctx.backend
    has_ufa = (
        effective_backend.capabilities.has_unguarded_filesystem_access
        if effective_backend
        else False
    )
    prompt_builder = functools.partial(
        _build_food_truck_prompt,
        mcp_prefix=detect_autoskillit_mcp_prefix(),
        has_unguarded_filesystem_access=has_ufa,
    )

    if disable_quota_guard:

        async def quota_checker(_cfg: object) -> dict[str, object]:
            return {"should_sleep": False}
    else:
        from autoskillit.execution import check_and_sleep_if_needed

        _supports_quota = (
            effective_backend.capabilities.anthropic_provider_capable
            if effective_backend
            else True
        )

        async def quota_checker(_cfg: object) -> dict[str, object]:
            return await check_and_sleep_if_needed(
                _cfg, provider="anthropic" if _supports_quota else ""
            )

    async def quota_refresher(_cfg: object) -> None:
        pass

    return await execute_dispatch(
        tool_ctx=ctx,
        recipe=recipe,
        task=task,
        ingredients=ingredients,
        dispatch_name=None,
        timeout_sec=timeout_sec,
        prompt_builder=prompt_builder,
        quota_checker=quota_checker,
        quota_refresher=quota_refresher,
        cache_invalidator=None,
        resume_session_id=resume_session_id,
        prior_dispatch_id=prior_dispatch_id,
        dispatch_backend=dispatch_backend,
        provider_capability_overrides=_prov_overrides,
        effective_backend_map=_effective_backend_map,
    )


def fleet_run(
    recipe: str,
    *,
    task: Annotated[str, Parameter(name=["--task", "-t"])] = "",
    ingredient: Annotated[tuple[str, ...], Parameter(name=["--ingredient", "-i"])] = (),
    backend: Annotated[str | None, Parameter(name=["--backend"])] = None,
    timeout_sec: Annotated[int | None, Parameter(name=["--timeout-sec"])] = None,
    resume_session_id: Annotated[str | None, Parameter(name=["--resume-session-id"])] = None,
    prior_dispatch_id: Annotated[str | None, Parameter(name=["--prior-dispatch-id"])] = None,
    disable_quota_guard: Annotated[bool, Parameter(name=["--disable-quota-guard"])] = False,
) -> None:
    """One-shot headless recipe dispatch (experimental).

    Dispatches a single recipe run non-interactively. Prints the dispatch
    result envelope as JSON on stdout. Exit 0 on SUCCESS, nonzero otherwise.
    """
    # --- Session-type guard (retained for headless — prevents recursive dispatch) ---
    if os.environ.get("AUTOSKILLIT_SESSION_TYPE") in ("skill", "leaf"):
        _fleet_run_error(
            "FLEET_SESSION_TYPE_BLOCKED",
            "'fleet run' cannot run inside a skill or leaf session.",
        )
    # NOTE: CLAUDECODE guard intentionally NOT applied — REQ-HFD-006

    # --- Config + feature gates ---
    from autoskillit.config import load_config

    try:
        cfg = load_config(Path.cwd())
    except Exception as exc:
        logger.error("fleet run: failed to load config", exc_info=True)
        _fleet_run_error("FLEET_CONFIG_ERROR", f"Failed to load config: {exc}")

    # Fleet base feature check (equivalent to _require_fleet but with JSON output)
    if not is_feature_enabled(
        "fleet", cfg.features, experimental_enabled=cfg.experimental_enabled
    ):
        _fleet_run_error(
            "FLEET_FEATURE_DISABLED",
            "The 'fleet' feature is not enabled.\n"
            "Enable with: features.experimental_enabled: true in your config\n"
            "Or set: AUTOSKILLIT_FEATURES__FLEET=true",
        )

    # Headless run feature check
    if not is_feature_enabled(
        "fleet_headless_run",
        cfg.features,
        experimental_enabled=cfg.experimental_enabled,
    ):
        _fleet_run_error(
            "FLEET_FEATURE_DISABLED",
            "The 'fleet_headless_run' feature is not enabled.\n"
            "Enable with: features.experimental_enabled: true\n"
            "Or: features.fleet_headless_run: true\n"
            "Or: AUTOSKILLIT_FEATURES__FLEET_HEADLESS_RUN=true",
        )

    # --- Parse ingredients ---
    ingredients: dict[str, str] | None = None
    if ingredient:
        ingredients = {}
        for item in ingredient:
            if "=" not in item:
                _fleet_run_error(
                    "FLEET_INVALID_ARGUMENT",
                    f"Ingredient must be key=value, got: {item!r}",
                )
            k, v = item.split("=", 1)
            ingredients[k] = v

    # --- Resolve backend ---
    dispatch_backend = None
    if backend is not None:
        from autoskillit.server import resolve_backend_override

        try:
            dispatch_backend = resolve_backend_override(backend)
        except ValueError as exc:
            _fleet_run_error("FLEET_INVALID_BACKEND", str(exc))

    # --- Run dispatch ---
    import asyncio

    from autoskillit.fleet import DispatchRejected, DispatchStatus

    try:
        result = asyncio.run(
            _execute_fleet_run(
                cfg=cfg,
                recipe=recipe,
                task=task,
                ingredients=ingredients,
                timeout_sec=timeout_sec,
                dispatch_backend=dispatch_backend,
                resume_session_id=resume_session_id,
                prior_dispatch_id=prior_dispatch_id,
                disable_quota_guard=disable_quota_guard,
            )
        )
    except KeyboardInterrupt as exc:
        logger.error("fleet run: dispatch interrupted: %s", exc)
        _fleet_run_error(
            "FLEET_DISPATCH_INTERRUPTED", "Dispatch interrupted by signal.", exit_code=1
        )
    except Exception as exc:
        logger.error("fleet run: dispatch crashed", exc_info=True)
        _fleet_run_error("FLEET_L3_STARTUP_OR_CRASH", str(exc))

    # --- Output result envelope ---
    print(result.outcome.to_envelope())

    # --- Exit code (DispatchRejected has no .success/.dispatch_status — check it first) ---
    if isinstance(result.outcome, DispatchRejected):
        raise SystemExit(3)
    if result.outcome.success:
        raise SystemExit(0)
    if result.outcome.dispatch_status == DispatchStatus.RESUMABLE:
        raise SystemExit(2)
    raise SystemExit(1)
