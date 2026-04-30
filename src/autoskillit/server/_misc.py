"""Quota, hook-config, triage, and miscellaneous server utilities."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import get_logger
from autoskillit.execution import check_and_sleep_if_needed
from autoskillit.hooks import _HOOK_CONFIG_PATH_COMPONENTS

if TYPE_CHECKING:
    from autoskillit.config import QuotaGuardConfig

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
    from autoskillit.execution import _refresh_quota_cache

    while True:
        await asyncio.sleep(config.cache_refresh_interval)
        try:
            await _refresh_quota_cache(config)
        except Exception as exc:
            logger.warning("quota_refresh_loop_error", exc_info=True, error=str(exc))
