"""Subprocess wrapper, dry-walkthrough check, and shared helpers for MCP tools."""

from __future__ import annotations

import asyncio
import functools
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    RESERVED_LOG_RECORD_KEYS,
    SessionType,
    TerminationReason,
    extract_path_arg,
    get_logger,
    session_type,
)
from autoskillit.execution import (
    SCENARIO_STEP_NAME_ENV,  # noqa: F401 — re-exported for tools_execution.py
    _refresh_quota_cache,  # noqa: F401 — re-exported for tools_execution.py; patched by tests
    check_and_sleep_if_needed,  # noqa: F401 — re-exported for tools_execution.py dispatch
    fetch_repo_merge_state,  # noqa: F401 — re-exported for tools_ci.py
    invalidate_cache,  # noqa: F401 — re-exported for tools_execution.py dispatch
    resolve_log_dir,  # noqa: F401 — used by tools_github.py, tools_status.py
    resolve_remote_name,  # noqa: F401 — re-exported for tools_git.py
    write_telemetry_clear_marker,  # noqa: F401 — used by tools_status.py
)
from autoskillit.hooks import _HOOK_CONFIG_PATH_COMPONENTS
from autoskillit.pipeline import gate_error_result, headless_error_result
from autoskillit.workspace import clone_registry  # noqa: F401 — re-exported for tools_clone.py

if TYPE_CHECKING:
    from fastmcp import Context

    from autoskillit.config import QuotaGuardConfig
    from autoskillit.core import SubprocessResult

logger = get_logger(__name__)

_HOOK_CONFIG_FILENAME: str = _HOOK_CONFIG_PATH_COMPONENTS[-1]
_HOOK_DIR_COMPONENTS: tuple[str, ...] = _HOOK_CONFIG_PATH_COMPONENTS[:-1]


def _hook_config_path(project_root: Path) -> Path:
    """Return the canonical path to the hook configuration JSON file."""
    return project_root.joinpath(*_HOOK_DIR_COMPONENTS, _HOOK_CONFIG_FILENAME)


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


def _get_ctx_or_none():  # type: ignore[return]
    """Deferred import of _get_ctx_or_none from _state to avoid circular imports."""
    from autoskillit.server._state import _get_ctx_or_none as _ctx_none_fn

    return _ctx_none_fn()


def track_response_size(
    tool_name: str,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator: measure the JSON string size of a tool response and record to response_log.

    Last-resort safety net. Tool implementations SHOULD catch exceptions locally
    and emit domain-specific envelopes with more helpful ``user_visible_message``
    values; this decorator only catches what slips through.

    Apply BELOW @mcp.tool() so the wrapped function is what FastMCP registers:

        @mcp.tool(tags={"automation"})
        @track_response_size("get_token_summary")
        async def get_token_summary(...) -> str:
            ...
    """

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                result = json.dumps(
                    {
                        "success": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "exit_code": -1,
                        "subtype": "tool_exception",
                        "user_visible_message": (
                            f"An internal error occurred in {tool_name}: "
                            f"{type(exc).__name__}. Run 'autoskillit doctor' or reinstall."
                        ),
                    }
                )
                logger.exception("Unhandled exception in tool %s", tool_name)
            try:
                ctx = _get_ctx_or_none()
                if ctx is not None:
                    response_str = result if isinstance(result, str) else json.dumps(result)
                    threshold = ctx.config.mcp_response.alert_threshold_tokens
                    exceeded = ctx.response_log.record(
                        tool_name, response_str, alert_threshold_tokens=threshold
                    )
                    if exceeded:
                        from fastmcp import Context as FmcpContext

                        mcp_ctx = next(
                            (a for a in args if isinstance(a, FmcpContext)),
                            next(
                                (v for v in kwargs.values() if isinstance(v, FmcpContext)),
                                None,
                            ),
                        )
                        if mcp_ctx is not None:
                            await _notify(
                                mcp_ctx,
                                "info",
                                f"MCP tool '{tool_name}' response exceeded "
                                f"{threshold} estimated token threshold",
                                logger_name="autoskillit.server.response_size",
                            )
            except Exception:
                logger.warning(
                    "track_response_size_failed",
                    tool_name=tool_name,
                    exc_info=True,
                )
            return result

        return wrapper

    return decorator


def _require_orchestrator_or_higher(tool_name: str = "") -> str | None:
    """Return headless_error JSON if session is leaf-tier; None if permitted.

    Interactive sessions (HEADLESS not set) always pass.
    Headless sessions must be orchestrator or fleet tier.
    Fail-closed: unset/invalid SESSION_TYPE → LEAF → deny.
    """
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        return None

    st = session_type()
    if st in (SessionType.ORCHESTRATOR, SessionType.FLEET):
        return None

    msg = (
        f"{tool_name} cannot be called from leaf sessions. "
        "Only orchestrator or fleet sessions may call this tool."
        if tool_name
        else None
    )
    return headless_error_result(msg)


def _require_orchestrator_exact(tool_name: str = "") -> str | None:
    """Return headless_error JSON if session is not orchestrator-tier; None if permitted.

    Interactive sessions (HEADLESS not set) always pass.
    Headless sessions must be exactly orchestrator tier.
    Fleet and leaf tiers are denied.
    """
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        return None

    st = session_type()
    if st is SessionType.ORCHESTRATOR:
        return None

    if st is SessionType.FLEET:
        msg = (
            f"{tool_name} cannot be called from {st.value} sessions. "
            f"{st.value.capitalize()} sessions have an auto-opened gate."
            " open_kitchen is unnecessary."
            if tool_name
            else None
        )
    else:
        msg = (
            f"{tool_name} cannot be called from leaf sessions. "
            "Only the orchestrator may call this tool."
            if tool_name
            else None
        )
    return headless_error_result(msg)


def _require_fleet(tool_name: str = "") -> str | None:
    """Return headless_error JSON if session is not fleet-tier; None if permitted.

    No interactive bypass — fleet is a specific tier, not a headless guard.
    """
    st = session_type()
    if st is SessionType.FLEET:
        return None

    msg = (
        f"{tool_name} requires a fleet session. Current session type is not fleet."
        if tool_name
        else None
    )
    return headless_error_result(msg)


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
    return []  # end delimiter never found


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
    env: Mapping[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a subprocess asynchronously with timeout. Returns (returncode, stdout, stderr).

    Delegates to run_managed_async which uses temp file I/O (immune to
    pipe-blocking from child FD inheritance) and psutil process tree cleanup.
    """
    runner = _get_ctx().runner
    assert runner is not None, "No subprocess runner configured"
    result = await runner(cmd, cwd=Path(cwd), timeout=timeout, env=env)
    return _process_runner_result(result, timeout)


def _check_dry_walkthrough(skill_command: str, cwd: str) -> str | None:
    """If skill_command is an implement skill, verify the plan has been dry-walked.

    Returns an error JSON string if validation fails, None if OK.
    """
    tokens = skill_command.strip().split()
    if not tokens or tokens[0] not in _get_config().implement_gate.skill_names:
        return None
    skill_name = tokens[0]
    plan_path_str = extract_path_arg(skill_command)
    if plan_path_str is None:
        return gate_error_result(f"Missing plan path argument for {skill_name}")
    plan_path = Path(cwd) / plan_path_str
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


async def infer_repo_from_remote(cwd: str, hint: str | None = None) -> str:
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
    from autoskillit.server import _get_ctx

    try:
        await check_and_sleep_if_needed(_get_ctx().config.quota_guard)
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


def _build_hook_diagnostic_warning() -> str | None:
    """Run hook health and drift checks. Return a warning string if issues are found.

    Only reads; never writes or modifies state. Returns None when all hooks are healthy
    or when settings.json does not yet exist (nothing to validate).
    """
    from autoskillit.hook_registry import (
        _claude_settings_path,
        _count_hook_registry_drift,
        find_broken_hook_scripts,
    )

    settings_path = _claude_settings_path("user")
    if not settings_path.exists():
        return None

    broken = find_broken_hook_scripts(settings_path)
    drift = _count_hook_registry_drift(settings_path)

    issues: list[str] = []
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
    if not issues:
        return None

    lines = ["\n⚠️  Hook configuration issues detected:"]
    for issue in issues:
        lines.append(f"   • {issue}")
    lines.append("   → Run 'autoskillit install' to regenerate hook configuration.\n")
    return "\n".join(lines)


def _get_food_truck_prompt_builder() -> Callable[..., str]:
    """Return the food truck prompt builder with mcp_prefix pre-bound."""
    from autoskillit.core import detect_autoskillit_mcp_prefix
    from autoskillit.fleet import _build_food_truck_prompt

    mcp_prefix = detect_autoskillit_mcp_prefix()
    return functools.partial(_build_food_truck_prompt, mcp_prefix=mcp_prefix)
