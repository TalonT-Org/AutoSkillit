"""Quota, hook-config, triage, and miscellaneous server utilities.

Also re-exports selected execution/workspace symbols so that tools_*.py files
(which are restricted to autoskillit.{core,pipeline,server,config,fleet} by
REQ-IMP-003 / REQ-ARCH-003) can access them without violating layer rules.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import get_logger
from autoskillit.execution import (
    SCENARIO_STEP_NAME_ENV as SCENARIO_STEP_NAME_ENV,
)
from autoskillit.execution import (
    _refresh_quota_cache as _refresh_quota_cache,
)
from autoskillit.execution import (
    check_and_sleep_if_needed as check_and_sleep_if_needed,
)
from autoskillit.execution import (
    fetch_repo_merge_state as fetch_repo_merge_state,
)
from autoskillit.execution import (
    invalidate_cache as invalidate_cache,
)
from autoskillit.execution import (
    resolve_log_dir as resolve_log_dir,
)
from autoskillit.execution import (
    resolve_remote_name as resolve_remote_name,
)
from autoskillit.execution import (
    write_telemetry_clear_marker as write_telemetry_clear_marker,
)
from autoskillit.hooks import _HOOK_CONFIG_PATH_COMPONENTS
from autoskillit.workspace import clone_registry as clone_registry

if TYPE_CHECKING:
    from autoskillit.config import QuotaGuardConfig
    from autoskillit.core import SkillResult

logger = get_logger(__name__)

_HOOK_CONFIG_FILENAME: str = _HOOK_CONFIG_PATH_COMPONENTS[-1]
_HOOK_DIR_COMPONENTS: tuple[str, ...] = _HOOK_CONFIG_PATH_COMPONENTS[:-1]


def _hook_config_path(project_root: Path) -> Path:
    """Return the canonical path to the hook configuration JSON file."""
    return project_root.joinpath(*_HOOK_DIR_COMPONENTS, _HOOK_CONFIG_FILENAME)


def _extract_block(text: str, start_delim: str, end_delim: str) -> list[str]:
    """Return all lines between start_delim and end_delim (exclusive).

    Returns an empty list if either delimiter is absent or the block is empty.
    Lines are returned as-is (no stripping) to preserve JSON-parseable content.
    """
    in_block = False
    block_lines: list[str] = []
    for line in text.splitlines():
        if line.strip() == start_delim:
            in_block = True
            continue
        if line.strip() == end_delim:
            if not in_block:
                return []
            return block_lines
        if in_block:
            block_lines.append(line)
    return []


def _build_hook_diagnostic_warning() -> str | None:
    """Run hook health and drift checks. Return a warning string if issues are found."""
    from autoskillit.core import DIRECT_INSTALL_CACHE_SUBDIR
    from autoskillit.hook_registry import (
        _claude_settings_path,
        _count_hook_registry_drift,
        find_broken_hook_scripts,
        validate_plugin_cache_hooks,
    )

    issues: list[str] = []

    settings_path = _claude_settings_path("user")
    if settings_path.exists():
        broken = find_broken_hook_scripts(settings_path)
        drift = _count_hook_registry_drift(settings_path)
        if broken:
            issues.append(f"Hook scripts not found: {', '.join(broken)}")
        if drift.orphaned > 0:
            issues.append(
                f"{drift.orphaned} orphaned hook entry(ies) in settings.json are not in "
                f"HOOK_REGISTRY — every matching tool call will be denied with ENOENT."
            )
        if drift.missing > 0:
            issues.append(
                f"{drift.missing} hook(s) from HOOK_REGISTRY are not deployed in settings.json."
            )

    _cache_dir = (
        Path.home() / ".claude" / "plugins" / "cache" / DIRECT_INSTALL_CACHE_SUBDIR / "autoskillit"
    )
    cache_broken = validate_plugin_cache_hooks(cache_dir=_cache_dir)
    if cache_broken:
        issues.append(
            f"Plugin cache has {len(cache_broken)} stale hook path(s): {', '.join(cache_broken)}"
        )

    if not issues:
        return None

    lines = ["\n⚠️  Hook configuration issues detected:"]
    for issue in issues:
        lines.append(f"   • {issue}")
    lines.append("   → Run 'autoskillit install' to regenerate hook configuration.\n")
    return "\n".join(lines)


async def _apply_triage_gate(
    result: dict[str, Any], name: str, recipe_info: Any = None
) -> dict[str, Any]:
    """Apply LLM triage to stale-contract suggestions, suppressing cosmetic ones.

    Delegates to the RecipeRepository implementation via the Composition Root.
    """
    from autoskillit.server._state import _ctx

    if _ctx is None or _ctx.recipes is None:
        return result

    from autoskillit._llm_triage import triage_staleness

    return await _ctx.recipes.apply_triage_gate(
        result, name, recipe_info, _ctx.temp_dir, logger, triage_fn=triage_staleness
    )


async def resolve_repo_from_remote(cwd: str, hint: str | None = None) -> str:
    """Return 'owner/repo' from git remote URL, or '' on failure.

    hint: optional owner/repo string or full GitHub URL; parsed before
          git remote inference. Passes through to resolve_remote_repo.
    """
    from autoskillit.execution import resolve_remote_repo

    return await resolve_remote_repo(cwd, hint=hint) or ""


async def _prime_quota_cache() -> None:
    """Fetch quota from the Anthropic API and write the local cache.

    Called at open_kitchen so the cache is primed before any run_skill hook fires.
    Fails open: a quota fetch failure must not abort kitchen open.
    """
    from autoskillit.server._state import _get_ctx as _ctx_fn

    try:
        await check_and_sleep_if_needed(_ctx_fn().config.quota_guard)
    except Exception:
        logger.warning("quota_prime_failed", exc_info=True)


async def _quota_refresh_loop(config: QuotaGuardConfig) -> None:
    """Long-running coroutine: refreshes the quota cache every cache_refresh_interval seconds.

    Designed to run as a background asyncio.Task for the duration of a kitchen session.
    The loop sleeps first, then refreshes — ensuring _prime_quota_cache's initial write
    is not immediately overwritten. CancelledError from asyncio.sleep propagates
    uncaught, terminating the loop cleanly when the task is cancelled.

    Guarantee: with cache_refresh_interval < cache_max_age, the cache written by any
    loop tick will still be fresh when the next tick fires. The hook never sees a stale
    cache as long as this loop is running.
    """
    while True:
        await asyncio.sleep(config.cache_refresh_interval)
        try:
            await _refresh_quota_cache(config)
        except Exception as exc:
            logger.warning("quota_refresh_loop_error", exc_info=True, error=str(exc))


def persist_run_skill_state(skill_result: SkillResult, project_dir: Path) -> None:
    import os  # noqa: PLC0415

    from autoskillit.core import ensure_project_temp  # noqa: PLC0415
    from autoskillit.execution import (  # noqa: PLC0415
        SessionState,
        persist_session_state,
    )

    if not skill_result.session_id:
        return
    try:
        state = SessionState(
            session_id=skill_result.session_id,
            pid=os.getpid(),
            boot_id="",
            starttime_ticks=0,
            infra_exit_category=skill_result.infra_exit_category
            if skill_result.infra_exit_category
            else None,
        )
        state_dir = ensure_project_temp(project_dir) / "session_state"
        persist_session_state(state, state_dir)
    except Exception:
        logger.debug("run_skill: could not persist session state", exc_info=True)


def clear_run_skill_state(project_dir: Path) -> None:
    from autoskillit.core import ensure_project_temp  # noqa: PLC0415
    from autoskillit.execution import (  # noqa: PLC0415
        clear_session_state,
    )

    try:
        state_dir = ensure_project_temp(project_dir) / "session_state"
        clear_session_state(state_dir)
    except Exception:
        logger.debug("run_skill: could not clear session state", exc_info=True)
