"""Gate helpers, dry-walkthrough check, and subprocess wrapper for MCP tools."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import RESERVED_LOG_RECORD_KEYS, TerminationReason, get_logger
from autoskillit.pipeline import gate_error_result
from autoskillit.recipe import (
    StaleItem,
    StalenessEntry,
    compute_recipe_hash,
    find_recipe_by_name,
    load_bundled_manifest,
    read_staleness_cache,
    write_staleness_cache,
)

if TYPE_CHECKING:
    from fastmcp import Context

    from autoskillit.execution.process import SubprocessResult

logger = get_logger(__name__)


async def _notify(
    ctx: Context,
    level: str,
    message: str,
    logger_name: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Send an MCP progress notification via FastMCP's Context.

    Validates extra dict keys against RESERVED_LOG_RECORD_KEYS before
    dispatching. Raises ValueError if any reserved key is found — this
    surfaces programming errors in tests rather than silently crashing
    at runtime only when DEBUG logging is active.

    Catches (RuntimeError, AttributeError, KeyError) from FastMCP internals:
    - RuntimeError: no active MCP session (Context.session raises)
    - AttributeError: ctx is CurrentContext() sentinel during testing
    - KeyError: makeRecord() collision (defense-in-depth; prevented by validation)
    """
    if extra:
        invalid = RESERVED_LOG_RECORD_KEYS & extra.keys()
        if invalid:
            raise ValueError(
                f"extra dict contains reserved LogRecord keys: {sorted(invalid)!r}. "
                "Rename these keys to avoid stdlib logging collisions."
            )
    try:
        if level == "info":
            await ctx.info(message, logger_name=logger_name, extra=extra)
        elif level == "error":
            await ctx.error(message, logger_name=logger_name, extra=extra)
    except (RuntimeError, AttributeError, KeyError):
        pass


def _get_ctx():  # type: ignore[return]
    """Deferred import of _get_ctx from _state to avoid circular imports."""
    from autoskillit.server._state import _get_ctx as _ctx_fn

    return _ctx_fn()


def _get_config():  # type: ignore[return]
    """Deferred import of _get_config from _state to avoid circular imports."""
    from autoskillit.server._state import _get_config as _cfg_fn

    return _cfg_fn()


def _require_enabled() -> str | None:
    """Return error JSON if tools are not enabled, None if OK.

    All tools are gated by default and can only be activated by the user
    typing the open_kitchen prompt. The prompt name is prefixed by Claude
    Code based on how the server was loaded (plugin vs --plugin-dir).
    This survives --dangerously-skip-permissions because MCP prompts are
    outside the permission system.
    """
    if not _get_ctx().gate.enabled:
        return gate_error_result()
    return None


def _validate_skill_command(skill_command: str) -> str | None:
    """Return error JSON if skill_command does not start with '/', None if OK."""
    if not skill_command.strip().startswith("/"):
        return gate_error_result(
            "run_skill requires a slash-command as skill_command.\n"
            f"Got: {skill_command!r}\n"
            "Expected: skill_command must start with '/' "
            "(e.g. /autoskillit:investigate, /autoskillit:make-plan, /audit-arch).\n"
            "Prose task descriptions are not valid skill invocations."
        )
    return None


async def _apply_triage_gate(
    result: dict[str, Any], name: str, recipe_info: Any = None
) -> dict[str, Any]:
    """Apply LLM triage to stale-contract suggestions, suppressing cosmetic ones.

    Checks the staleness cache for a cached triage result. If not cached,
    runs triage_staleness() (a 30s Haiku call) for hash_mismatch items only.
    version_mismatch items are always treated as meaningful and never suppressed.

    When ``recipe_info`` is provided by the caller, the internal find() call is
    skipped, eliminating the duplicate YAML directory scan.

    Modifies ``result`` in-place and returns it.
    """
    from autoskillit.server._state import _ctx

    if _ctx is None or _ctx.recipes is None:
        return result

    stale_suggs = [s for s in result.get("suggestions", []) if s.get("rule") == "stale-contract"]
    if not stale_suggs:
        return result

    if recipe_info is None:
        recipe_info = _ctx.recipes.find(name, Path.cwd())
    if recipe_info is None:
        return result

    cache_path = Path.cwd() / ".autoskillit" / "temp" / "recipe_staleness_cache.json"
    t0 = time.perf_counter()
    cached = read_staleness_cache(cache_path, name)
    logger.debug(
        "triage_gate_cache_read",
        recipe=name,
        elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
    )

    if cached is not None and cached.triage_result == "cosmetic":
        result["suggestions"] = [
            s for s in result["suggestions"] if s.get("rule") != "stale-contract"
        ]
        return result

    if cached is None or cached.triage_result is None:
        hash_items = [
            StaleItem(
                skill=s["skill"],
                reason=s["reason"],
                stored_value=s.get("stored_value", ""),
                current_value=s.get("current_value", ""),
            )
            for s in stale_suggs
            if s.get("reason") == "hash_mismatch"
        ]
        if hash_items:
            from datetime import UTC, datetime

            from autoskillit._llm_triage import triage_staleness

            t_llm = time.perf_counter()
            triage = await triage_staleness(hash_items)
            logger.debug(
                "triage_gate_llm_triage",
                recipe=name,
                elapsed_ms=round((time.perf_counter() - t_llm) * 1000, 1),
            )
            all_cosmetic = all(not r.get("meaningful", True) for r in triage)
            triage_str = "cosmetic" if all_cosmetic else "meaningful"
            current_hash = compute_recipe_hash(recipe_info.path)
            current_ver = load_bundled_manifest().get("version", "")
            write_staleness_cache(
                cache_path,
                name,
                StalenessEntry(
                    recipe_hash=current_hash,
                    manifest_version=current_ver,
                    is_stale=True,
                    triage_result=triage_str,
                    checked_at=datetime.now(UTC).isoformat(),
                ),
            )
            if all_cosmetic and not any(
                s.get("reason") == "version_mismatch" for s in stale_suggs
            ):
                result["suggestions"] = [
                    s for s in result["suggestions"] if s.get("rule") != "stale-contract"
                ]

    return result


def _process_runner_result(
    result: SubprocessResult,
    timeout: float,
) -> tuple[int, str, str]:
    """Convert a SubprocessResult to (returncode, stdout, stderr).

    Translates TIMED_OUT termination into (-1, stdout, "Process timed out after {timeout}s").
    Shared by _run_subprocess (helpers.py) and _run_git (git.py).
    """
    if result.termination == TerminationReason.TIMED_OUT:
        return -1, result.stdout, f"Process timed out after {timeout}s"
    return result.returncode, result.stdout, result.stderr


async def _run_subprocess(
    cmd: list[str],
    *,
    cwd: str,
    timeout: float,
) -> tuple[int, str, str]:
    """Run a subprocess asynchronously with timeout. Returns (returncode, stdout, stderr).

    Delegates to run_managed_async which uses temp file I/O (immune to
    pipe-blocking from child FD inheritance) and psutil process tree cleanup.
    """
    runner = _get_ctx().runner
    assert runner is not None, "No subprocess runner configured"
    result = await runner(cmd, cwd=Path(cwd), timeout=timeout)
    return _process_runner_result(result, timeout)


def _check_dry_walkthrough(skill_command: str, cwd: str) -> str | None:
    """If skill_command is an implement skill, verify the plan has been dry-walked.

    Returns an error JSON string if validation fails, None if OK.
    """
    parts = skill_command.strip().split(None, 1)
    if not parts or parts[0] not in _get_config().implement_gate.skill_names:
        return None

    skill_name = parts[0]

    if len(parts) < 2:
        return gate_error_result(f"Missing plan path argument for {skill_name}")

    plan_path = Path(cwd) / parts[1].strip().strip('"').strip("'")
    if not plan_path.is_file():
        return gate_error_result(f"Plan file not found: {plan_path}")

    # TOCTOU acceptance (option c, per P1-5): This function reads the plan file
    # once here. If the file is modified or deleted between this gate check and
    # the headless session acting on it, the gate condition will be stale.
    # This is an accepted limitation: the plan file is user-controlled, and
    # modification between check and execution is a user error. Options (a) and
    # (b) — passing file content via stdin or re-verifying via content hash —
    # involve significant scope and are deferred.
    first_line = plan_path.read_text().split("\n", 1)[0].strip()
    if first_line != _get_config().implement_gate.marker:
        return gate_error_result(
            f"Plan has NOT been dry-walked. Run /dry-walkthrough on the plan first. "
            f"Expected first line: {_get_config().implement_gate.marker!r}, "
            f"actual: {first_line[:100]!r}"
        )

    return None


async def _import_and_call(
    dotted_path: str,
    args: dict[str, object] | None = None,
    timeout: float = 30,
) -> dict[str, object]:
    """Import a Python callable by dotted path and invoke it.

    Returns dict with 'success', 'result' (or 'error').
    Handles sync and async callables, with timeout protection.
    """
    import asyncio
    import importlib
    import inspect

    if args is None:
        args = {}

    if "." not in dotted_path:
        return {"success": False, "error": f"Invalid dotted path: {dotted_path!r}"}

    module_path, attr_name = dotted_path.rsplit(".", 1)

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        return {"success": False, "error": f"Import failed for {module_path!r}: {exc}"}

    try:
        func = getattr(module, attr_name)
    except AttributeError:
        return {
            "success": False,
            "error": f"Module {module_path!r} has no attribute {attr_name!r}",
        }

    if not callable(func):
        return {"success": False, "error": f"{dotted_path!r} is not callable"}

    try:
        if inspect.iscoroutinefunction(func):
            result = await asyncio.wait_for(func(**args), timeout=timeout)
        else:
            result = await asyncio.wait_for(asyncio.to_thread(func, **args), timeout=timeout)
    except TimeoutError:
        logger.warning(
            "run_python timed out; sync thread may continue running",
            dotted_path=dotted_path,
            timeout=timeout,
        )
        return {"success": False, "error": f"Timeout after {timeout}s calling {dotted_path}"}
    except Exception as exc:
        logger.warning(
            "run_python execution failed",
            dotted_path=dotted_path,
            error=type(exc).__name__,
        )
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        import json as _json

        _json.dumps(result)
        return {"success": True, "result": result}
    except (TypeError, ValueError):
        return {"success": True, "result": str(result)}


def _find_recipe(name: str, cwd: Path) -> Any:
    """Look up a recipe by name. Delegates to recipe layer; exposed for tools_kitchen.py.

    tools_kitchen.py (a tools_*.py file) is restricted by REQ-IMP-003 to importing
    only from autoskillit.core, autoskillit.pipeline, and autoskillit.server.
    This function provides the architecture-compliant bridge to autoskillit.recipe.
    """
    return find_recipe_by_name(name, cwd)


async def _prime_quota_cache() -> None:
    """Fetch quota from the Anthropic API and write the local cache.

    Called at open_kitchen so the cache is primed before any run_skill hook fires.
    Fails open: a quota fetch failure must not abort kitchen open.
    """
    from autoskillit.execution import check_and_sleep_if_needed
    from autoskillit.server import _get_ctx

    try:
        await check_and_sleep_if_needed(_get_ctx().config.quota_guard)
    except (OSError, ValueError, RuntimeError):
        logger.warning("quota_prime_failed", exc_info=True)
