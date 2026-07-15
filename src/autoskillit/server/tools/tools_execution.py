"""MCP tool handlers: run_cmd, run_python, run_skill."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import regex as re
import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    CODEX_SESSIONS_SUBDIR,
    DISPATCH_ID_ENV_VAR,
    SKILL_CAPABILITY_REGISTRY,
    SKILL_COMMAND_DISPLAY_MAX,
    WORKTREE_SKILLS,
    ClaudeDirectoryConventions,
    ClosureAuthoritySpec,
    CodingAgentBackend,
    SkillResult,
    ValidatedAddDir,
    WriteBehaviorSpec,
    closure_authority_spec_from_args,
    execution_marker,
    extract_skill_name,
    find_caller_session_id,
    get_logger,
    is_feature_enabled,
    is_git_worktree,
    parse_plan_paths,
    resolve_target_skill,
    truncate_text,
)
from autoskillit.core import current_order_id as _current_order_id
from autoskillit.core import current_step_name as _current_step_name
from autoskillit.core import resolve_skill_temp_dir as _resolve_skill_temp_dir
from autoskillit.pipeline import canonical_step_name as _canonical_step_name
from autoskillit.pipeline import gate_error_result
from autoskillit.server import mcp
from autoskillit.server._guards import (
    _check_dry_walkthrough,
    _check_input_contracts,
    _check_recipe_read_prohibition,
    _check_write_target_boundary,
    _require_enabled,
    _require_orchestrator_or_higher,
    _validate_skill_command,
)
from autoskillit.server._misc import (
    SCENARIO_STEP_NAME_ENV,
    _hook_config_overlay_path,
    _pipeline_tracker_dir,
    _pipeline_tracker_path,
    resolve_closure_write_dirs,
)
from autoskillit.server._misc import (
    get_backend as _get_backend,
)
from autoskillit.server._notify import _notify, track_response_size
from autoskillit.server._subprocess import _run_subprocess
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._execution_helpers import (
    _import_and_call,
    maybe_promote_work_dir,
    resolve_relative_path_args,
    validate_path_arg_anchoring,
)
from autoskillit.server.tools._preflight import _get_fix_required_hook_matchers
from autoskillit.server.tools._types import ToolFailureEnvelope

logger = get_logger(__name__)

_PURE_SLEEP_RE = re.compile(
    r'^(?:python3?\s+-c\s+["\']import time;\s*time\.sleep\((?P<py_secs>\d+(?:\.\d+)?)\)["\']'
    r"|sleep\s+(?P<sh_secs>\d+(?:\.\d+)?))$"
)

INGREDIENT_LOCK_DENY_PREFIX = "INGREDIENT LOCK ENFORCED"
DEPENDENCY_DENY_PREFIX = "DEPENDENCY UNMET"


def _is_absolute_path(path: str) -> bool:
    """Return True if path is an absolute filesystem path."""
    return Path(path).is_absolute()


def _is_backend_incompatible(skill_info: object, effective_backend: str) -> bool:
    """Return True if skill's backend_requirements exclude effective_backend."""
    reqs = getattr(skill_info, "backend_requirements", None)
    return bool(reqs and effective_backend not in reqs)


def _check_backend_compat(
    skill_command: str,
    resolved_command: str,
    effective_order_id: str,
    target_name: str | None,
    skill_info: object | None,
    effective_backend_obj: CodingAgentBackend | None,
    skill_resolver: object | None,
) -> str | None:
    """Fail-closed backend compatibility gate.

    Returns crash JSON if the skill's backend_requirements exclude the effective
    backend, or if the check cannot be conclusively performed (missing resolver
    or backend). Returns None on pass.
    """
    if target_name is None:
        return None
    if skill_resolver is None:
        return SkillResult.crashed(
            exception=RuntimeError(
                f"Cannot verify backend compatibility for skill {target_name!r}: "
                "skill resolver is not available."
            ),
            skill_command=resolved_command,
            order_id=effective_order_id,
        ).to_json()
    if effective_backend_obj is None:
        return SkillResult.crashed(
            exception=RuntimeError(
                f"Cannot dispatch skill {target_name!r}: session backend is not configured."
            ),
            skill_command=resolved_command,
            order_id=effective_order_id,
        ).to_json()
    if skill_info is None:
        return None
    effective_backend = effective_backend_obj.name
    if _is_backend_incompatible(skill_info, effective_backend):
        return SkillResult.crashed(
            exception=RuntimeError(
                f"Skill {target_name!r} requires backend "
                f"{sorted(getattr(skill_info, 'backend_requirements', []))} but session "
                f"backend is {effective_backend!r}."
            ),
            skill_command=resolved_command,
            order_id=effective_order_id,
        ).to_json()
    fix_required_matchers = _get_fix_required_hook_matchers(
        effective_backend_obj.capabilities.applicable_guards,
    )
    if fix_required_matchers:
        return SkillResult.crashed(
            exception=RuntimeError(
                f"Cannot dispatch skill {target_name!r} on backend "
                f"{effective_backend!r}: HOOK_REGISTRY contains fix-required "
                f"entries [{', '.join(fix_required_matchers)}] that cannot be "
                f"enforced by this backend."
            ),
            skill_command=resolved_command,
            order_id=effective_order_id,
        ).to_json()
    return None


def _check_ingredient_locks(step_name: str, order_id: str) -> str | None:
    """Check if step_name is locked out by ingredient locks. Returns deny JSON or None."""
    from autoskillit.server import _get_ctx  # circular-break

    ctx = _get_ctx()
    overlay_path = _hook_config_overlay_path(ctx.project_dir)
    if not overlay_path.exists():
        return None
    try:
        overlay = json.loads(overlay_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    locked_steps = overlay.get("locked_steps", {})
    effective_oid = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

    if effective_oid and effective_oid in locked_steps:
        if locked_steps[effective_oid].get(step_name) is False:
            ingredient_info = overlay.get("locked_ingredients", {}).get(effective_oid, {})
            return json.dumps(
                {
                    "success": False,
                    "is_error": True,
                    "error": (
                        f"{INGREDIENT_LOCK_DENY_PREFIX}: Step '{step_name}' is locked out. "
                        f"Locked ingredients for pipeline '{effective_oid}': {ingredient_info}. "
                        f"Call lock_ingredients(unlock=[...]) to release."
                    ),
                }
            )
    elif not effective_oid:
        for pid, steps in locked_steps.items():
            if steps.get(step_name) is False:
                return json.dumps(
                    {
                        "success": False,
                        "is_error": True,
                        "error": (
                            f"{INGREDIENT_LOCK_DENY_PREFIX}: Step '{step_name}' is locked out "
                            f"by pipeline '{pid}'. Pass order_id to scope the check, "
                            f"or call lock_ingredients(unlock=[...]) to release."
                        ),
                    }
                )
    return None


def _active_order_ids_for_kitchen(ctx: ToolContext) -> set[str]:
    """Return distinct order_ids with an order-id-scoped tracker under this kitchen.

    Used by the `_check_pipeline_deps` kitchen-scoped fallback to detect when
    multiple pipelines are concurrently active under one kitchen (e.g.
    fleet-style parallel dispatch). In that case the kitchen_id must not be
    used as an aliasing key — one pipeline's completed step could otherwise
    falsely satisfy an unrelated pipeline's dependency.
    """
    tracker_dir = _pipeline_tracker_dir(ctx.project_dir)
    if not tracker_dir.is_dir():
        return set()
    active: set[str] = set()
    for path in tracker_dir.glob("*.json"):
        if path.stem == ctx.kitchen_id:
            continue
        try:
            tracker = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if tracker.get("kitchen_id") == ctx.kitchen_id:
            active.add(path.stem)
    return active


def _check_pipeline_deps(step_name: str, order_id: str) -> str | None:
    """Check if step_name's dependencies are satisfied. Returns deny JSON or None."""
    effective_oid = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")
    from autoskillit.server import _get_ctx  # circular-break

    ctx = _get_ctx()
    if not effective_oid:
        # Kitchen-scoped fallback: interactive sessions have a kitchen_id but no
        # order_id. Only resolve to the kitchen-scoped tracker when at most one
        # pipeline is active under this kitchen.
        if not ctx.kitchen_id:
            return None
        active_oids = _active_order_ids_for_kitchen(ctx)
        if len(active_oids) > 1:
            return json.dumps(
                {
                    "success": False,
                    "is_error": True,
                    "error": (
                        f"{DEPENDENCY_DENY_PREFIX}: multiple pipelines are active "
                        f"under this kitchen ({sorted(active_oids)}). Pass order_id "
                        "explicitly to scope the dependency check."
                    ),
                }
            )
        effective_oid = ctx.kitchen_id
    tracker_path = _pipeline_tracker_path(ctx.project_dir, effective_oid)
    if not tracker_path.exists():
        return None
    try:
        tracker = json.loads(tracker_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    canonical = _canonical_step_name(step_name)
    deps = tracker.get("dependencies", {}).get(canonical, [])
    if not deps:
        return None
    steps = tracker.get("steps", {})
    unmet = [d for d in deps if steps.get(d, {}).get("status") not in ("complete", "skipped")]
    if not unmet:
        return None
    dep_status = {d: steps.get(d, {}).get("status", "unknown") for d in unmet}
    return json.dumps(
        {
            "success": False,
            "is_error": True,
            "error": (
                f"{DEPENDENCY_DENY_PREFIX}: Step '{step_name}' requires {unmet} to complete "
                f"first. Pipeline '{effective_oid}': {dep_status}."
            ),
        }
    )


def _resolve_step_name_from_recipe(
    skill_command: str,
    active_recipe_steps: dict[str, object],
) -> tuple[str, bool]:
    """Resolve step_name from active_recipe_steps by matching skill_command prefix.

    Returns (step_name, is_ambiguous):
    - (name, False) when exactly one recipe step matches
    - ("", True) when multiple steps match (ambiguous)
    - ("", False) when no steps match
    """
    cmd_prefix = skill_command.split()[0] if skill_command.strip() else ""
    if not cmd_prefix:
        return ("", False)
    matches: list[str] = []
    for step_key, step_obj in active_recipe_steps.items():
        with_args = getattr(step_obj, "with_args", None)
        if not isinstance(with_args, dict):
            continue
        step_sc = with_args.get("skill_command", "")
        if step_sc and step_sc.split()[0] == cmd_prefix:
            matches.append(step_key)
    if len(matches) == 1:
        return (matches[0], False)
    return ("", len(matches) > 1)


def _has_active_locks(order_id: str) -> bool:
    """Return True if any ingredient locks are actively denying steps."""
    from autoskillit.server import _get_ctx  # circular-break

    ctx = _get_ctx()
    overlay_path = _hook_config_overlay_path(ctx.project_dir)
    if not overlay_path.exists():
        return False
    try:
        overlay = json.loads(overlay_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    locked_steps = overlay.get("locked_steps", {})
    if not locked_steps:
        return False
    effective_oid = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")
    if effective_oid:
        return any(v is False for v in locked_steps.get(effective_oid, {}).values())
    return any(v is False for steps in locked_steps.values() for v in steps.values())


def _has_active_deps() -> bool:
    """Return True if a kitchen-scoped tracker exists with any dependencies defined."""
    from autoskillit.server import _get_ctx  # circular-break

    ctx = _get_ctx()
    if not ctx.kitchen_id:
        return False
    tracker_path = _pipeline_tracker_path(ctx.project_dir, ctx.kitchen_id)
    if not tracker_path.exists():
        return False
    try:
        tracker = json.loads(tracker_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return bool(tracker.get("dependencies"))


def _check_review_approach_plan_path(step_name: str, skill_command: str) -> str | None:
    """Return a deny JSON if review_approach is invoked without a plan-path argument.

    review_approach requires a plan file path produced by rectify/make_plan.
    Heuristic: the first argument after the skill name must not be a bare
    issue URL, which would otherwise fall back to ambiguous "conversation
    context" inference instead of failing loudly.
    """
    if _canonical_step_name(step_name) != "review_approach":
        return None
    parts = skill_command.split()
    if len(parts) < 2:
        return None
    first_arg = parts[1]
    if first_arg.startswith("https://") or first_arg.startswith("http://"):
        return json.dumps(
            {
                "success": False,
                "is_error": True,
                "error": (
                    "review_approach requires a plan file path argument (a path "
                    "under the project's temp directory produced by "
                    "rectify/make_plan), not an issue URL."
                ),
            }
        )
    return None


def _derive_run_cmd_write_prefixes() -> tuple[str, ...]:
    """Read allowed write prefixes from environment.

    Mirrors the env-var resolution logic in hooks/guards/write_guard.py:
    AUTOSKILLIT_ALLOWED_WRITE_PREFIXES (colon-separated) takes precedence over
    AUTOSKILLIT_ALLOWED_WRITE_PREFIX (single value).
    """
    multi = os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", "")
    if multi:
        return tuple(p for p in multi.split(":") if p)
    single = os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "")
    if single:
        return (single,)
    return ()


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield(result_type="run_cmd")
@track_response_size("run_cmd")
async def run_cmd(
    cmd: str,
    cwd: str,
    timeout: int = 600,
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Run an arbitrary shell command in the specified directory.

    Args:
        cmd: The full command to run (e.g. "make build").
        cwd: Working directory for the command.
        timeout: Max seconds before killing the process (default 600).
        step_name: Optional YAML step key for wall-clock timing accumulation.

    Never raises.
    """
    if (tier_gate := _require_orchestrator_or_higher("run_cmd")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    if (gate := _check_recipe_read_prohibition(cmd=cmd)) is not None:
        return gate
    if (
        gate := _check_write_target_boundary(cmd, cwd, _derive_run_cmd_write_prefixes())
    ) is not None:
        return gate
    try:
        with structlog.contextvars.bound_contextvars(tool="run_cmd", cwd=cwd):
            if not _derive_run_cmd_write_prefixes():
                logger.debug(
                    "run_cmd: no write prefixes configured — write boundary guard inactive"
                )
            logger.info("run_cmd", cmd=cmd[:80], cwd=cwd)
            await _notify(
                ctx, "info", f"run_cmd: {cmd[:80]}", "autoskillit.run_cmd", extra={"cwd": cwd}
            )

            from autoskillit.server import _get_ctx  # circular-break

            tool_ctx = _get_ctx()
            _start = time.monotonic()
            try:
                m = _PURE_SLEEP_RE.match(cmd.strip())
                if m:
                    seconds = float(m.group("py_secs") or m.group("sh_secs"))
                    await asyncio.sleep(seconds)
                    return json.dumps(
                        {"success": True, "exit_code": 0, "stdout": "", "stderr": ""}
                    )
                _env: dict[str, str] | None = (
                    {**os.environ, SCENARIO_STEP_NAME_ENV: step_name} if step_name else None
                )
                returncode, stdout, stderr = await _run_subprocess(
                    ["bash", "-c", cmd],
                    cwd=cwd,
                    timeout=float(timeout),
                    env=_env,
                )
                result = {
                    "success": returncode == 0,
                    "exit_code": returncode,
                    "stdout": truncate_text(stdout),
                    "stderr": truncate_text(stderr),
                }
                if not result["success"]:
                    await _notify(
                        ctx,
                        "error",
                        "run_cmd failed",
                        "autoskillit.run_cmd",
                        extra={"exit_code": returncode},
                    )
                return json.dumps(result)
            finally:
                if step_name:
                    tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.error("run_cmd unhandled exception", exc_info=True)
        return json.dumps(
            {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
            }
        )


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield(result_type="run_python")
@track_response_size("run_python")
async def run_python(
    callable: str,
    args: dict[str, object] | None = None,
    timeout: int = 30,
    work_dir: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Call a Python function directly by dotted module path.

    Imports the module, resolves the function, and calls it with the
    provided arguments. Use for lightweight decision logic that does
    not need an LLM session (counter checks, status lookups, eligibility
    decisions).

    Both sync and async functions are supported. Async functions are
    awaited directly; sync functions run in a thread pool.

    Args:
        callable: Dotted path to the function (e.g. "mypackage.module.function").
        args: Keyword arguments to pass to the function.
        timeout: Max seconds before aborting the call (default 30).
        work_dir: When set, relative path-like args (output_dir, etc.) are
            anchored to this directory before the callable is invoked.

    Never raises.
    """
    if (tier_gate := _require_orchestrator_or_higher("run_python")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    if (gate := _check_recipe_read_prohibition(callable_name=callable)) is not None:
        return gate
    try:
        with structlog.contextvars.bound_contextvars(tool="run_python"):
            logger.info("run_python", callable=callable, timeout=timeout)
            await _notify(
                ctx,
                "info",
                f"run_python: {callable}",
                "autoskillit.run_python",
                extra={"callable": callable},
            )
            anchor_err = validate_path_arg_anchoring(args, work_dir)
            if anchor_err:
                return json.dumps({"success": False, "error": anchor_err})
            promoted = maybe_promote_work_dir(args, work_dir)
            if promoted != work_dir:
                logger.warning(
                    "run_python auto-promoted work_dir from args to tool level",
                    callable=callable,
                    work_dir=promoted,
                )
                work_dir = promoted
            if work_dir and not Path(work_dir).is_absolute():
                return json.dumps(
                    {
                        "success": False,
                        "error": f"run_python: work_dir must be absolute, got {work_dir!r}",
                    }
                )
            resolved_args = args
            if work_dir:
                resolved_args = resolve_relative_path_args(args or {}, work_dir)
            result = await _import_and_call(callable, args=resolved_args, timeout=float(timeout))
            if not result.get("success"):
                await _notify(
                    ctx,
                    "error",
                    "run_python failed",
                    "autoskillit.run_python",
                    extra={"callable": callable},
                )
            return json.dumps(result)
    except Exception as exc:
        logger.error("run_python unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


def _persist_run_skill_state(skill_result: SkillResult, project_dir: Path) -> None:
    from autoskillit.server._misc import persist_run_skill_state  # circular-break

    persist_run_skill_state(skill_result, project_dir)


def _clear_run_skill_state(project_dir: Path) -> None:
    from autoskillit.server._misc import clear_run_skill_state  # circular-break

    clear_run_skill_state(project_dir)


def _compute_write_prefixes(
    write_watch_dirs: list[Path],
    cwd: str,
    skill_command: str,
) -> tuple[str, tuple[str, ...]]:

    worktree_write_prefixes: list[str] = []
    extracted = extract_skill_name(skill_command)
    if write_watch_dirs and extracted and extracted in WORKTREE_SKILLS:
        resolved_cwd = Path(cwd).resolve()
        if is_git_worktree(resolved_cwd):
            # cwd IS the worktree — include cwd itself and its parent (the worktrees/ dir)
            worktree_write_prefixes.append(str(resolved_cwd) + "/")
            worktree_write_prefixes.append(str(resolved_cwd.parent) + "/")
        else:
            nested_wt = resolved_cwd / "worktrees"
            sibling_wt = resolved_cwd.parent / "worktrees"
            if nested_wt.is_dir():
                worktree_write_prefixes.append(str(nested_wt) + "/")
            if sibling_wt.is_dir():
                worktree_write_prefixes.append(str(sibling_wt) + "/")
            if not nested_wt.is_dir() and not sibling_wt.is_dir():
                worktree_write_prefixes.append(str(sibling_wt) + "/")

    base_prefixes = [str(d.resolve()) + "/" for d in write_watch_dirs]
    all_prefixes = base_prefixes + worktree_write_prefixes
    return base_prefixes[0] if base_prefixes else "", tuple(all_prefixes)


def _scope_covers_cwd(allowed_write_prefixes: tuple[str, ...], cwd: str) -> bool:
    """Return True if any allowed_write_prefix covers cwd (lexical prefix match)."""
    if not allowed_write_prefixes or not cwd:
        return False
    resolved_cwd_str = str(Path(cwd).resolve()).rstrip("/") + "/"
    for pfx in allowed_write_prefixes:
        if resolved_cwd_str.startswith(pfx):
            return True
    return False


def _aggregate_sandbox_overrides(skill_caps: frozenset[str]) -> frozenset[str]:
    """Aggregate required_sandbox_overrides from all declared capabilities."""
    return frozenset().union(
        *(
            SKILL_CAPABILITY_REGISTRY[cap].required_sandbox_overrides
            for cap in skill_caps
            if cap in SKILL_CAPABILITY_REGISTRY
        )
    )


def _has_routing_capability(skill_caps: frozenset[str]) -> bool:
    """Return True if any declared capability is worker_routable (triggers backend reroute)."""
    return any(
        SKILL_CAPABILITY_REGISTRY.get(cap) is not None
        and SKILL_CAPABILITY_REGISTRY[cap].worker_routable
        for cap in skill_caps
    )


def _get_routing_caps(skill_caps: frozenset[str]) -> list[str]:
    """Return sorted list of worker_routable capabilities that trigger backend reroute."""
    return sorted(
        cap
        for cap in skill_caps
        if SKILL_CAPABILITY_REGISTRY.get(cap) and SKILL_CAPABILITY_REGISTRY[cap].worker_routable
    )


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("run_skill")
async def run_skill(
    skill_command: str,
    cwd: str,
    model: str = "",
    step_name: str = "",
    step_provider: str = "",
    order_id: str = "",
    stale_threshold: int | None = None,
    idle_output_timeout: int | None = None,
    output_dir: str = "",
    resume_session_id: str = "",
    closure_authority_path: str = "",
    closure_authority_hash: str = "",
    closure_plan_paths: str = "",
    closure_base_sha: str = "",
    closure_diff_sha: str = "",
    closure_target_sha: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Run a Claude Code headless session with a skill command.

    Returns JSON with: success, result, session_id, subtype, is_error, exit_code,
    needs_retry, retry_reason. When needs_retry is true, retry_reason is:
    - "resume": context/turn limit hit — partial progress on disk, route to on_context_limit.
    - "drain_race": channel confirmed completion but stdout not fully flushed — route to
      on_context_limit (same as resume).
    - "completed_no_flush": session exited with empty stdout but write evidence confirms work was
      performed — route to on_context_limit (same as drain_race/resume).
    - "empty_output": session exited cleanly with no output AND no write evidence — no partial
      progress, route to on_failure.
    - "path_contamination": session wrote files outside its working directory — route to
      on_failure.
    - "early_stop": model stopped before completion marker — route to on_failure.
    - "zero_writes": skill made no writes despite write expectation — route to on_failure.
    - "thinking_stall": model produced thinking blocks only, no text/tool output — route to
      on_context_limit if lifespan_started, else on_failure.
    - "contract_recovery": model completed and wrote artifacts but structured output failed
      pattern validation and nudge could not recover — route to on_context_limit if
      has_progress_evidence, else on_failure.

    This is the correct MCP tool to delegate work to a headless session during
    pipeline execution. NEVER use native tools (Read, Grep, Glob, Edit, Write,
    Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator.
    All code changes, investigation, and research happen through the headless
    session launched by this tool.

    Use this for all skill sessions, including long-running ones that may hit the
    context limit. The 2-hour timeout is the default. When needs_retry is true,
    route to the appropriate resume step (e.g., retry-worktree) rather than
    re-running this step from scratch.

    Args:
        skill_command: The full prompt including skill invocation (e.g. "/investigate ...").
        cwd: Working directory for the claude session.
        model: Model to use (e.g. "sonnet", "opus"). Empty string = use config default.
        step_name: Optional YAML step key (e.g. "implement"). When set, token usage is
            accumulated in the server-side token log, grouped by this name.
        order_id: Optional per-issue/order identifier for token telemetry scoping. When set,
            token and timing entries are keyed by this value, enabling per-issue isolation
            in get_token_summary/get_timing_summary and in the token_summary_appender hook.
        stale_threshold: Override the staleness kill threshold in seconds. When set on
            a RecipeStep, the recipe orchestrator passes it here. None uses the global
            config default (RunSkillConfig.stale_threshold, default 1200s).
        idle_output_timeout: Override the idle stdout kill threshold in seconds.
            0 = disabled for this step. None = use global config
            (RunSkillConfig.idle_output_timeout, default 1000s).
        resume_session_id: Session ID from a previous run_skill call that was interrupted.
            When set, the session is resumed via --resume instead of starting fresh.
            The skill_command becomes a continuation instruction (non-slash text is allowed).
            Pass the session_id from the previous run_skill result JSON.

    Never raises.
    """
    if (tier_gate := _require_orchestrator_or_higher("run_skill")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    if not resume_session_id and (cmd_error := _validate_skill_command(skill_command)) is not None:
        return cmd_error
    if cwd and not _is_absolute_path(cwd):
        return json.dumps(
            {
                "success": False,
                "error": (
                    f"run_skill: cwd must be an absolute path, got: {cwd!r}. "
                    "Check that the skill resolved the worktree_path to absolute "
                    '(e.g. WORKTREE_PATH="$(cd "${WORKTREE_PATH}" && pwd)").'
                ),
            }
        )
    if cwd and not os.path.isdir(cwd):
        return json.dumps(
            {
                "success": False,
                "error": f"run_skill: cwd does not exist: {cwd}",
            }
        )
    if (
        step_name
        and not resume_session_id
        and (_lock_denial := _check_ingredient_locks(step_name, order_id)) is not None
    ):
        return _lock_denial
    if (
        step_name
        and not resume_session_id
        and (_dep_denial := _check_pipeline_deps(step_name, order_id)) is not None
    ):
        return _dep_denial
    if (
        step_name
        and not resume_session_id
        and (_plan_path_denial := _check_review_approach_plan_path(step_name, skill_command))
        is not None
    ):
        return _plan_path_denial
    try:
        _sn_token = _oid_token = None
        from autoskillit.server import _get_ctx  # circular-break

        _cleanup_session_id: str | None = None
        tool_ctx = _get_ctx()

        if not step_name and not resume_session_id and tool_ctx.active_recipe_steps:
            _resolved, _ambiguous = _resolve_step_name_from_recipe(
                skill_command, tool_ctx.active_recipe_steps
            )
            if _resolved:
                step_name = _resolved
                logger.warning(
                    "step_name_resolved_from_recipe",
                    step=step_name,
                    command=skill_command[:80],
                )
                if (_lock_denial := _check_ingredient_locks(step_name, order_id)) is not None:
                    return _lock_denial
                if (_dep_denial := _check_pipeline_deps(step_name, order_id)) is not None:
                    return _dep_denial
                if (
                    _plan_path_denial := _check_review_approach_plan_path(step_name, skill_command)
                ) is not None:
                    return _plan_path_denial
            elif _ambiguous:
                if _has_active_deps():
                    return json.dumps(
                        {
                            "success": False,
                            "is_error": True,
                            "error": (
                                f"{DEPENDENCY_DENY_PREFIX}: step_name is empty and matched "
                                "multiple recipe steps by skill_command prefix (ambiguous). "
                                "Cannot verify dependency status. Pass step_name explicitly."
                            ),
                        }
                    )
            elif _has_active_locks(order_id):
                return json.dumps(
                    {
                        "success": False,
                        "is_error": True,
                        "error": (
                            f"{INGREDIENT_LOCK_DENY_PREFIX}: step_name is empty and could "
                            "not be resolved from the recipe. Cannot verify lock "
                            "status. Pass step_name explicitly or call "
                            "lock_ingredients(unlock=[...]) to release all locks."
                        ),
                    }
                )
            elif _has_active_deps():
                return json.dumps(
                    {
                        "success": False,
                        "is_error": True,
                        "error": (
                            f"{DEPENDENCY_DENY_PREFIX}: step_name is empty and could "
                            "not be resolved from the recipe. Cannot verify dependency "
                            "status. Pass step_name explicitly."
                        ),
                    }
                )

        with structlog.contextvars.bound_contextvars(tool="run_skill", cwd=cwd):
            logger.info("run_skill", command=skill_command[:80], cwd=cwd)
            await _notify(
                ctx,
                "info",
                f"run_skill: {skill_command[:80]}",
                "autoskillit.run_skill",
                extra={"cwd": cwd, "model": model or "default"},
            )

            from autoskillit.server import _get_config  # circular-break

            # Auto-enrich order_id from the fleet dispatcher's env variable when the
            # caller did not pass an explicit value. AUTOSKILLIT_DISPATCH_ID is injected
            # by fleet/_api.py into every L2 food truck session environment and inherited by all
            # sub-sessions, ensuring token log entries carry the correct order_id without
            # requiring recipe authors to thread it through every run_skill call.
            effective_order_id = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

            if not resume_session_id:
                if (
                    input_error := _check_input_contracts(
                        skill_command, cwd, tool_ctx.input_contract_resolver
                    )
                ) is not None:
                    return input_error

            if _get_config().safety.require_dry_walkthrough:
                if (gate_error := _check_dry_walkthrough(skill_command, cwd)) is not None:
                    return gate_error

            if tool_ctx.executor is None:
                return json.dumps({"success": False, "error": "Executor not configured"})

            provider_extras: dict[str, str] | None = None
            profile_name_out: str = ""
            effective_model = model

            _cfg = _get_config()
            _in_fleet_dispatch = bool(os.environ.get(DISPATCH_ID_ENV_VAR))
            _inspector_model = _cfg.fleet.inspector_model if _in_fleet_dispatch else ""

            if not step_provider and step_name and tool_ctx.active_recipe_steps is not None:
                _recipe_step_pre = tool_ctx.active_recipe_steps.get(step_name)
                if _recipe_step_pre is not None and _recipe_step_pre.provider:
                    step_provider = _recipe_step_pre.provider
                    logger.warning(
                        "step_provider_resolved_from_recipe",
                        step=step_name,
                        provider=step_provider,
                    )

            if is_feature_enabled(
                "providers", _cfg.features, experimental_enabled=_cfg.experimental_enabled
            ):
                from autoskillit.server._guards import (  # circular-break
                    _resolve_model_as_profile,
                    _resolve_provider_profile,
                )

                _profile, _env_dict = _resolve_provider_profile(
                    step_name or "",
                    tool_ctx.recipe_name or "",
                    _cfg.providers,
                    step_provider=step_provider or "",
                )
                if _profile != "anthropic":
                    provider_extras = _env_dict
                    profile_name_out = _profile
                else:
                    effective_model, prof_name, prof_extras = _resolve_model_as_profile(
                        model, _cfg.providers
                    )
                    if prof_extras is not None:
                        provider_extras = prof_extras
                        profile_name_out = prof_name

            if _cfg.model.model_override:
                effective_model = _cfg.model.model_override
            else:
                if tool_ctx.recipe_name:
                    _mo_recipe_map = _cfg.providers.model_overrides.get(tool_ctx.recipe_name)
                    if _mo_recipe_map:
                        _step_mo = _mo_recipe_map.get(step_name) if step_name else None
                        if _step_mo is None:
                            _step_mo = _mo_recipe_map.get("*")
                        if _step_mo:
                            effective_model = _step_mo

            # Resolve correct namespace and prepare for tier2 activation.
            # Must precede backend_override — _skill_info feeds skill-requirement check.
            resolved_command = skill_command
            target_name: str | None = None
            if tool_ctx.skill_resolver is not None:
                resolved_command, target_name = resolve_target_skill(
                    skill_command, tool_ctx.skill_resolver
                )
            _skill_info = (
                tool_ctx.skill_resolver.resolve(target_name)
                if tool_ctx.skill_resolver and target_name
                else None
            )
            if target_name and _skill_info is None:
                return SkillResult.crashed(
                    exception=RuntimeError(
                        f"Skill '{target_name}' not found in any discovery source "
                        f"(bundled skills/, skills_extended/, or project-local). "
                        f"Cannot launch session for undiscoverable skill name."
                    ),
                    skill_command=resolved_command,
                    order_id=effective_order_id,
                ).to_json()

            _provider_override = (
                provider_extras
                and "ANTHROPIC_BASE_URL" in provider_extras
                and tool_ctx.backend is not None
                and not tool_ctx.backend.capabilities.anthropic_provider_capable
            )

            # Explicit config backend override — highest authority, suppresses
            # capability-driven routing for this step (REQ-RES-001).
            from autoskillit.server._guards import _resolve_backend_override  # circular-break

            _explicit_backend_override: str | None = _resolve_backend_override(
                step_name or "",
                tool_ctx.recipe_name or "",
                _cfg.agent_backend,
            )

            _skill_caps: frozenset[str] = (
                getattr(_skill_info, "uses_capabilities", frozenset())
                if _skill_info
                else frozenset()
            )
            _sandbox_overrides = _aggregate_sandbox_overrides(_skill_caps)
            _network_access = "sandbox_workspace_write.network_access=true" in _sandbox_overrides
            _has_routing_cap = _has_routing_capability(_skill_caps)
            _routing_caps = _get_routing_caps(_skill_caps) if _has_routing_cap else []
            _skill_requires_claude = bool(
                _has_routing_cap
                and tool_ctx.backend is not None
                and not tool_ctx.backend.capabilities.anthropic_provider_capable
            )

            # When an explicit backend override pins a step to a non-claude
            # backend, capability-driven routing must NOT crash on the missing
            # claude binary — the operator has explicitly chosen the backend.
            if _skill_requires_claude and _explicit_backend_override is None:
                if shutil.which("claude") is None:
                    return SkillResult.crashed(
                        exception=RuntimeError(
                            f"Skill {target_name!r} requires claude-code backend "
                            f"({', '.join(_routing_caps)} capability) but 'claude' binary "
                            f"is not found on PATH. Install Claude Code CLI to "
                            f"enable capability-driven routing."
                        ),
                        skill_command=resolved_command,
                        order_id=effective_order_id,
                    ).to_json()

            # If an explicit override points to a non-claude backend whose
            # binary is absent, fail closed with a clear message.
            if _explicit_backend_override is not None and _explicit_backend_override != (
                tool_ctx.backend.name if tool_ctx.backend else None
            ):
                try:
                    _explicit_backend_obj_check = _get_backend(_explicit_backend_override)
                except Exception:
                    logger.warning(
                        "explicit_backend_resolve_failed",
                        backend=_explicit_backend_override,
                        exc_info=True,
                    )
                    return SkillResult.crashed(
                        exception=RuntimeError(
                            f"Step explicitly pinned to backend "
                            f"{_explicit_backend_override!r} but that backend "
                            f"is not registered. Check step_overrides / "
                            f"recipe_overrides for typos."
                        ),
                        skill_command=resolved_command,
                        order_id=effective_order_id,
                    ).to_json()
                if _explicit_backend_obj_check is not None:
                    _explicit_binary = getattr(
                        _explicit_backend_obj_check.capabilities, "process_name", ""
                    )
                    if _explicit_binary and shutil.which(_explicit_binary) is None:
                        return SkillResult.crashed(
                            exception=RuntimeError(
                                f"Step explicitly pinned to backend "
                                f"{_explicit_backend_override!r} but required binary "
                                f"{_explicit_binary!r} is not found on PATH."
                            ),
                            skill_command=resolved_command,
                            order_id=effective_order_id,
                        ).to_json()

            if _explicit_backend_override is not None:
                backend_override = _explicit_backend_override
            elif _provider_override or _skill_requires_claude:
                backend_override = AGENT_BACKEND_CLAUDE_CODE
            else:
                backend_override = None

            _effective_backend_obj: CodingAgentBackend | None = (
                _get_backend(backend_override)
                if backend_override is not None and tool_ctx.backend is not None
                else tool_ctx.backend
            )

            _backend_override_source: str | None = None
            if _explicit_backend_override is not None:
                _backend_override_source = "explicit_config"
            elif _skill_requires_claude:
                _backend_override_source = "skill_requirement"
            elif _provider_override:
                _backend_override_source = "provider_profile"

            if backend_override:
                _override_reasons: list[str] = (
                    [_backend_override_source] if _backend_override_source else []
                )
                logger.info(
                    "backend_override_activated",
                    reason=(
                        _override_reasons[0] if len(_override_reasons) == 1 else _override_reasons
                    ),
                    skill=skill_command,
                    original_backend=tool_ctx.backend.name if tool_ctx.backend else "none",
                    target_backend=backend_override,
                    routing_capabilities=_routing_caps,
                )

            # Look up artifact validation patterns from skill contract
            expected_output_patterns: list[str] = []
            if tool_ctx.output_pattern_resolver:
                expected_output_patterns = list(tool_ctx.output_pattern_resolver(skill_command))

            # Look up write-expectation metadata from skill contract
            write_spec: WriteBehaviorSpec | None = None
            if tool_ctx.write_expected_resolver:
                write_spec = tool_ctx.write_expected_resolver(skill_command)

            # Resolve closure spec from explicit MCP tool parameters.
            # Closure args are first-class parameters (not embedded in skill_command text)
            # because the skill_command string is prompt text consumed by the LLM session,
            # not parsed by Python code.
            closure_spec: ClosureAuthoritySpec | None = closure_authority_spec_from_args(
                path=closure_authority_path or None,
                hash_=closure_authority_hash or None,
                plan_paths=parse_plan_paths(closure_plan_paths) if closure_plan_paths else (),
                base_sha=closure_base_sha,
                diff_sha=closure_diff_sha,
                target_sha=closure_target_sha,
            )

            # Build validated add_dirs via DefaultSessionSkillManager
            from uuid import uuid4

            # Backend compatibility gate — fail-closed, fires before replay and live session paths.
            if compat_error := _check_backend_compat(
                skill_command=skill_command,
                resolved_command=resolved_command,
                effective_order_id=effective_order_id,
                target_name=target_name,
                skill_info=_skill_info,
                effective_backend_obj=_effective_backend_obj,
                skill_resolver=tool_ctx.skill_resolver,
            ):
                return compat_error

            # Server-side recipe step parameter resolution.
            # When a step_name is provided and the recipe's step definition is cached,
            # auto-fill parameters the LLM may have omitted.
            if step_name and tool_ctx.active_recipe_steps is not None:
                _recipe_step = tool_ctx.active_recipe_steps.get(step_name)
                if _recipe_step is not None:
                    if not output_dir and "output_dir" in _recipe_step.with_args:
                        _recipe_output_dir = _recipe_step.with_args["output_dir"]
                        # Skip values containing unresolved template references —
                        # load() returns raw YAML without ingredient resolution,
                        # so ${{ context.* }} placeholders may survive.
                        if "${{" not in _recipe_output_dir:
                            output_dir = _recipe_output_dir
                            logger.warning(
                                "output_dir_resolved_from_recipe",
                                step=step_name,
                                output_dir=output_dir,
                            )

                    if stale_threshold is None and _recipe_step.stale_threshold is not None:
                        stale_threshold = _recipe_step.stale_threshold
                        logger.warning(
                            "stale_threshold_resolved_from_recipe",
                            step=step_name,
                            value=stale_threshold,
                        )

                    if (
                        idle_output_timeout is None
                        and _recipe_step.idle_output_timeout is not None
                    ):
                        idle_output_timeout = _recipe_step.idle_output_timeout
                        logger.warning(
                            "idle_output_timeout_resolved_from_recipe",
                            step=step_name,
                            value=idle_output_timeout,
                        )

            closure_report_root: Path | None = None
            if output_dir and closure_spec:
                _closure_root = Path(output_dir)
                if not _closure_root.is_absolute():
                    _closure_root = Path(cwd) / output_dir
                closure_report_root = _closure_root
            elif closure_spec and not output_dir:
                return json.dumps(
                    ToolFailureEnvelope(
                        success=False,
                        error=(
                            "closure_spec requires output_dir to locate"
                            " the closure report, but output_dir is empty"
                        ),
                        stage="validate_args:run_skill",
                        retriable=False,
                    )
                )

            write_watch_dirs: list[Path] = []
            if output_dir:
                resolved_dir = Path(output_dir)
                if not resolved_dir.is_absolute():
                    resolved_dir = Path(cwd) / output_dir
                write_watch_dirs.append(resolved_dir)

            if not write_watch_dirs:
                _default_temp = _resolve_skill_temp_dir(cwd, skill_command)
                if _default_temp:
                    write_watch_dirs.append(_default_temp)

            is_read_only = bool(
                tool_ctx.read_only_resolver and tool_ctx.read_only_resolver(skill_command)
            )
            completion_required = bool(
                tool_ctx.completion_required_resolver
                and tool_ctx.completion_required_resolver(skill_command)
            )
            invocation_marker = f"%%ORDER_UP::{uuid4().hex[:8]}%%"

            skill_add_dirs: list[ValidatedAddDir] = []
            replay_snapshot_used = False
            _runner = tool_ctx.runner
            if (
                step_name
                and _runner is not None
                and getattr(_runner, "skill_snapshots", None)
                and hasattr(_runner, "restore_skill_snapshot")
                and tool_ctx.ephemeral_root is not None
            ):
                _ephemeral_root = tool_ctx.ephemeral_root
                session_id = f"headless-{uuid4().hex[:12]}"
                _cleanup_session_id = session_id
                _restored = _runner.restore_skill_snapshot(  # type: ignore[attr-defined]
                    step_name, _ephemeral_root, session_id
                )
                if _restored is not None:
                    if not Path(_restored.path).is_dir():
                        logger.warning(
                            "stale_snapshot_path",
                            session_id=session_id,
                            path=_restored.path,
                        )
                        return SkillResult.crashed(
                            exception=RuntimeError(
                                f"Snapshot path {_restored.path!r} does not exist. "
                                f"The /dev/shm directory may have been reclaimed."
                            ),
                            skill_command=resolved_command,
                            session_id=session_id,
                            order_id=effective_order_id,
                        ).to_json()
                    skill_add_dirs.append(_restored)
                    replay_snapshot_used = True
                    logger.debug(
                        "replay_skill_snapshot_restored",
                        step=step_name,
                        session_id=session_id,
                    )

            if not replay_snapshot_used and tool_ctx.session_skill_manager is not None:
                allow_only: frozenset[str] | None = None
                closure: frozenset[str] = frozenset()
                if target_name:
                    closure = tool_ctx.session_skill_manager.compute_skill_closure(target_name)
                    if not closure:
                        return SkillResult.crashed(
                            exception=RuntimeError(
                                f"Skill '{target_name}' resolved to an empty closure. "
                                f"This indicates the skill exists but has no injectable content."
                            ),
                            skill_command=resolved_command,
                            order_id=effective_order_id,
                        ).to_json()
                    allow_only = closure

                session_id = f"headless-{uuid4().hex[:12]}"
                _cleanup_session_id = session_id
                session_root = tool_ctx.session_skill_manager.init_session(
                    session_id,
                    cook_session=False,
                    config=tool_ctx.config,
                    project_dir=tool_ctx.project_dir,
                    recipe_packs=tool_ctx.active_recipe_packs,
                    recipe_features=tool_ctx.active_recipe_features,
                    allow_only=allow_only,
                    backend=_effective_backend_obj,
                )
                if not tool_ctx.session_skill_manager.validate_session_exists(session_id):
                    logger.warning(
                        "stale_session_path",
                        session_id=session_id,
                        path=session_root.path,
                    )
                    return SkillResult.crashed(
                        exception=RuntimeError(
                            f"Session path {session_root.path!r} does not exist. "
                            f"The /dev/shm directory may have been reclaimed."
                        ),
                        skill_command=resolved_command,
                        session_id=session_id,
                        order_id=effective_order_id,
                    ).to_json()
                skill_add_dirs.append(session_root)

                if target_name:
                    tool_ctx.session_skill_manager.activate_skill_deps(session_id, target_name)
                    _is_known_skill = (
                        tool_ctx.skill_resolver is not None
                        and tool_ctx.skill_resolver.resolve(target_name) is not None
                    )
                    if _is_known_skill:
                        _skills_subdir = (
                            _effective_backend_obj.conventions.skills_subdir
                            if _effective_backend_obj is not None
                            else ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
                        )
                        _skill_md = (
                            Path(session_root.path) / _skills_subdir / target_name / "SKILL.md"
                        )
                        if not _skill_md.exists():
                            logger.error(
                                "target_skill_not_in_session",
                                target=target_name,
                                session_id=session_id,
                                session_root=str(session_root.path),
                            )
                            return SkillResult.crashed(
                                exception=RuntimeError(
                                    f"Target skill {target_name!r} not available in session "
                                    f"{session_id!r}: SKILL.md not found after init_session + "
                                    f"activate_skill_deps. Check tier/feature/pack gating."
                                ),
                                skill_command=resolved_command,
                                session_id=session_id,
                                order_id=effective_order_id,
                            ).to_json()
                    else:
                        # Defense-in-depth: should be unreachable after the pre-init
                        # existence gate (3a), but if reached, reject rather than proceed.
                        logger.error(
                            "unknown_skill_reached_init",
                            target=target_name,
                            session_id=session_id,
                        )
                        return SkillResult.crashed(
                            exception=RuntimeError(
                                f"Skill '{target_name}' unknown to resolver after session init. "
                                f"This should have been caught by the pre-init existence gate."
                            ),
                            skill_command=resolved_command,
                            session_id=session_id,
                            order_id=effective_order_id,
                        ).to_json()
                    # Replay-path sessions inherit write scope from their original
                    # snapshot — they don't need re-augmentation because the snapshot
                    # was built from a live session that already had the full prefix set.
                    if closure and tool_ctx.skill_resolver is not None:
                        write_watch_dirs.extend(
                            resolve_closure_write_dirs(
                                closure, tool_ctx.skill_resolver, cwd, write_watch_dirs
                            )
                        )

            allowed_write_prefix = ""
            allowed_write_prefixes: tuple[str, ...] = ()
            if write_watch_dirs:
                allowed_write_prefix, allowed_write_prefixes = _compute_write_prefixes(
                    write_watch_dirs, cwd, skill_command
                )
            elif is_read_only:
                _skill_temp_name = target_name or ""
                if _skill_temp_name:
                    allowed_write_prefix = os.path.join(
                        cwd, ".autoskillit", "temp", _skill_temp_name, ""
                    )
                else:
                    logger.warning(
                        "read_only_skill_no_target_name",
                        skill_command=skill_command[:SKILL_COMMAND_DISPLAY_MAX],
                    )

            # Preflight: for WORKTREE_SKILLS dispatches, the computed scope must cover cwd
            # so the session can write to its own tracked tree. Fail-fast BEFORE spawning
            # a session — otherwise the session locks itself out and burns N turns.
            if allowed_write_prefixes and target_name and target_name in WORKTREE_SKILLS and cwd:
                if not _scope_covers_cwd(allowed_write_prefixes, cwd):
                    return gate_error_result(
                        f"Write scope does not cover target worktree: "
                        f"cwd={cwd!r} not under any allowed prefix "
                        f"{allowed_write_prefixes!r}. "
                        f"Likely missing output_dir or malformed dispatch."
                    )

            _sn_token = _current_step_name.set(_canonical_step_name(step_name))
            _oid_token = _current_order_id.set(effective_order_id)

            _marker_dir: Path | None = (
                tool_ctx.backend.session_locator().project_log_dir(str(tool_ctx.project_dir))
                if tool_ctx.backend is not None
                else None
            )
            _orchestrator_sid = find_caller_session_id(project_dir=tool_ctx.project_dir)

            _start = time.monotonic()
            try:
                try:
                    with anyio.fail_after(_cfg.run_skill.mcp_tool_timeout_sec):
                        async with execution_marker(
                            _marker_dir,
                            _orchestrator_sid,
                            "run-skill",
                        ):
                            skill_result = await tool_ctx.executor.run(
                                resolved_command,
                                cwd,
                                model=effective_model,
                                add_dirs=skill_add_dirs,
                                step_name=step_name,
                                kitchen_id=tool_ctx.kitchen_id,
                                order_id=effective_order_id,
                                expected_output_patterns=expected_output_patterns,
                                write_behavior=write_spec,
                                stale_threshold=float(stale_threshold)
                                if stale_threshold is not None
                                else None,
                                idle_output_timeout=float(idle_output_timeout)
                                if idle_output_timeout is not None
                                else None,
                                completion_marker=invocation_marker,
                                recipe_name=tool_ctx.recipe_name,
                                recipe_content_hash=tool_ctx.recipe_content_hash,
                                recipe_composite_hash=tool_ctx.recipe_composite_hash,
                                recipe_version=tool_ctx.recipe_version,
                                allowed_write_prefix=allowed_write_prefix,
                                allowed_write_prefixes=allowed_write_prefixes,
                                readonly_skill=is_read_only,
                                completion_required=completion_required,
                                write_watch_dirs=write_watch_dirs,
                                provider_extras=provider_extras,
                                profile_name=profile_name_out,
                                provider_name=profile_name_out,
                                backend_override=backend_override,
                                backend_override_source=_backend_override_source,
                                resume_session_id=resume_session_id,
                                marker_dir=_marker_dir,
                                caller_session_id=_orchestrator_sid,
                                inspector_eligible=_in_fleet_dispatch and bool(_inspector_model),
                                inspector_model=_inspector_model,
                                network_access=_network_access,
                                closure_spec=closure_spec,
                                closure_report_root=closure_report_root,
                            )
                except TimeoutError as exc:
                    logger.error(
                        "run_skill_mcp_tool_timeout",
                        timeout_sec=_cfg.run_skill.mcp_tool_timeout_sec,
                    )
                    _timeout_exc = TimeoutError(
                        f"MCP tool timeout ({_cfg.run_skill.mcp_tool_timeout_sec}s) exceeded"
                    )
                    _timeout_exc.__cause__ = exc
                    return SkillResult.crashed(
                        exception=_timeout_exc,
                        skill_command=resolved_command,
                        order_id=effective_order_id,
                    ).to_json()
                if skill_result.success:
                    tool_ctx.audit.record_success(skill_command)
                    _clear_run_skill_state(tool_ctx.project_dir)
                else:
                    await _notify(
                        ctx,
                        "error",
                        "run_skill failed",
                        "autoskillit.run_skill",
                        extra={
                            "exit_code": skill_result.exit_code,
                            "subtype": skill_result.subtype,
                        },
                    )
                    _persist_run_skill_state(skill_result, tool_ctx.project_dir)
                if effective_order_id:
                    skill_result.order_id = effective_order_id
                from autoskillit.server._misc import (  # circular-break
                    _refresh_quota_cache,
                )

                if tool_ctx.background is not None:
                    tool_ctx.background.submit(
                        _refresh_quota_cache(tool_ctx.config.quota_guard),
                        label="quota_post_run_refresh",
                    )
                _json_str = skill_result.to_json()
                try:
                    _parsed = json.loads(_json_str)
                except Exception as exc:
                    logger.warning("run_skill_json_parse_failed", exc_info=True)
                    return json.dumps(
                        ToolFailureEnvelope(
                            success=False,
                            error=f"Degraded SkillResult payload: JSON parse failed: {exc}",
                            stage="validate_result:run_skill",
                            retriable=True,
                        )
                    )
                _missing = {"success", "exit_code"} - _parsed.keys()
                if _missing:
                    logger.warning(
                        "run_skill_degraded_payload",
                        absent_fields=sorted(_missing),
                    )
                    return json.dumps(
                        ToolFailureEnvelope(
                            success=False,
                            error=f"Degraded SkillResult payload: missing keys {sorted(_missing)}",
                            stage="validate_result:run_skill",
                            retriable=True,
                        )
                    )
                return _json_str
            except Exception as exc:
                logger.error("run_skill executor raised unexpectedly", exc_info=True)
                return SkillResult.crashed(
                    exception=exc,
                    skill_command=resolved_command,
                    order_id=effective_order_id,
                ).to_json()
            finally:
                if step_name:
                    tool_ctx.timing_log.record(
                        step_name, time.monotonic() - _start, order_id=effective_order_id
                    )
    except Exception as exc:
        logger.error("run_skill unhandled exception", exc_info=True)
        return SkillResult.crashed(
            exception=exc,
            skill_command=skill_command,
            order_id=order_id,
        ).to_json()
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            logger.warning("run_skill cancelled", exc_info=True)
        _cmd = locals().get("resolved_command", skill_command)
        _oid = locals().get("effective_order_id", order_id)
        return SkillResult.cancelled(
            skill_command=_cmd,  # type: ignore[arg-type]
            order_id=_oid,  # type: ignore[arg-type]
        ).to_json()
    finally:
        if _sn_token is not None:
            _current_step_name.reset(_sn_token)  # type: ignore[possibly-undefined]
        if _oid_token is not None:
            _current_order_id.reset(_oid_token)  # type: ignore[possibly-undefined]
        _sid: str | None = locals().get("_cleanup_session_id")  # type: ignore[assignment]
        if _sid is not None:
            _ssm = tool_ctx.session_skill_manager  # type: ignore[possibly-undefined]
            if _ssm is not None:
                try:
                    _ssm.cleanup_session(_sid)
                except Exception:
                    logger.warning(
                        "session_skill_cleanup_failed",
                        session_id=_sid,
                        exc_info=True,
                    )
            elif tool_ctx.ephemeral_root is not None:  # type: ignore[possibly-undefined]
                _cleanup_dir = tool_ctx.ephemeral_root / _sid  # type: ignore[possibly-undefined]
                if _cleanup_dir.is_dir():
                    try:
                        shutil.rmtree(_cleanup_dir)
                    except Exception:
                        logger.warning(
                            "session_dir_rmtree_failed", path=str(_cleanup_dir), exc_info=True
                        )
                else:
                    _codex_fallback = tool_ctx.temp_dir / CODEX_SESSIONS_SUBDIR / _sid
                    if _codex_fallback.is_dir():
                        try:
                            shutil.rmtree(_codex_fallback)
                        except Exception:
                            logger.warning(
                                "session_dir_rmtree_failed",
                                path=str(_codex_fallback),
                                exc_info=True,
                            )
