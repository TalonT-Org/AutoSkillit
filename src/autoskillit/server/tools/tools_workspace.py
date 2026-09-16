"""MCP tool handlers for worktree testing, commits, and workspace reset."""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import (
    CommitFailureClass,
    SpillSpec,
    WorkspaceOutcomeKind,
    WorkspaceOutcomeRecord,
    WorktreeGateContention,
    get_logger,
    resolve_temp_dir,
    spill_output,
    truncate_text,
)
from autoskillit.execution import build_sanitized_env
from autoskillit.server import mcp
from autoskillit.server._misc import condense_test_output
from autoskillit.server._notify import _notify, track_response_size
from autoskillit.server._subprocess import _run_subprocess
from autoskillit.server.lifecycle._guards import _require_enabled
from autoskillit.server.recipe._recipe_segment_delivery import (
    PreparedRecipeSegmentDelivery,
    attach_recipe_segment,
    prepare_recipe_segment_delivery,
)
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

if TYPE_CHECKING:
    from autoskillit.core import TestResult
    from autoskillit.pipeline import ToolContext

logger = get_logger(__name__)


def _bounded_test_stream(text: str, spec: SpillSpec, artifact_path: str | None) -> str:
    if len(text) <= spec.inline_max_chars:
        return text
    head = text[: spec.head_chars]
    tail = text[-spec.tail_chars :] if spec.tail_chars else ""
    marker = f"[raw test output spilled -> {artifact_path}]"
    return "\n".join(part for part in (head, marker, tail) if part)


def _build_test_check_response(
    tool_ctx: ToolContext,
    test_result: TestResult,
    resolved: str,
    *,
    timed_out: bool,
    effective_passed: bool,
) -> dict[str, object]:
    raw_artifact_path: str | None = None
    spec = SpillSpec(
        inline_max_chars=tool_ctx.config.output_budget.inline_max_chars,
        head_chars=tool_ctx.config.output_budget.head_chars,
        tail_chars=tool_ctx.config.output_budget.tail_chars,
    )
    condensed_stdout, condensed_stderr = condense_test_output(test_result)
    if not effective_passed:
        raw_output = json.dumps({"stdout": test_result.stdout, "stderr": test_result.stderr})
        spill_dir = (
            resolve_temp_dir(Path(resolved), tool_ctx.config.workspace.temp_dir) / "test_check"
        )
        spill_spec = spec.with_forced_spill(timed_out)
        raw_spill = spill_output(raw_output, spill_dir, "raw_output", spill_spec)
        raw_artifact_path = raw_spill.artifact_path
        if raw_artifact_path is None and (
            len(condensed_stdout) > spec.inline_max_chars
            or len(condensed_stderr) > spec.inline_max_chars
        ):
            raw_spill = spill_output(
                raw_output,
                spill_dir,
                "raw_output",
                spec.with_forced_spill(True),
            )
            raw_artifact_path = raw_spill.artifact_path
    response = {
        "passed": effective_passed,
        "timed_out": timed_out,
        "stdout": _bounded_test_stream(condensed_stdout, spec, raw_artifact_path),
        "stderr": _bounded_test_stream(condensed_stderr, spec, raw_artifact_path),
    }
    if test_result.outer_timeout_seconds is not None:
        response["outer_timeout_seconds"] = test_result.outer_timeout_seconds
    if raw_artifact_path is not None:
        response["raw_output_artifact_path"] = raw_artifact_path
    if test_result.duration_seconds is not None:
        response["duration_seconds"] = round(test_result.duration_seconds, 2)
    if test_result.filter_mode is not None:
        response["filter_mode"] = test_result.filter_mode
    if test_result.tests_selected is not None:
        response["tests_selected"] = test_result.tests_selected
    if test_result.tests_deselected is not None:
        response["tests_deselected"] = test_result.tests_deselected
    if test_result.full_run_reason is not None:
        response["full_run_reason"] = test_result.full_run_reason
    return response


_PRE_COMMIT_INFRASTRUCTURE_SIGNATURES = frozenset(
    {
        "os error 30",
        "read-only file system",
    }
)
_PRE_COMMIT_CACHE_PATH_SIGNATURES = frozenset({".cache", "/cache/", "uv-cache"})


def _combined_process_output(stderr: str, stdout: str, fallback: str) -> str:
    combined = "\n".join(part for part in (stderr.strip(), stdout.strip()) if part)
    return combined or fallback


def _pre_commit_failure_class(output: str) -> CommitFailureClass:
    normalized = output.casefold()
    known_infrastructure_failure = any(
        signature in normalized for signature in _PRE_COMMIT_INFRASTRUCTURE_SIGNATURES
    )
    cache_permission_failure = "permission denied" in normalized and any(
        signature in normalized for signature in _PRE_COMMIT_CACHE_PATH_SIGNATURES
    )
    if known_infrastructure_failure or cache_permission_failure:
        return CommitFailureClass.HOOK_INFRASTRUCTURE
    return CommitFailureClass.HOOK_REJECTED


async def _run_pre_commit_transaction(
    cwd: str,
    paths: list[str],
    *,
    workspace_temp_dir: str | None,
) -> dict[str, object] | None:
    pre_commit_bin = shutil.which("pre-commit", path=os.environ.get("PATH", ""))
    uv_bin = shutil.which("uv", path=os.environ.get("PATH", ""))
    hook_cmd: list[str] | None = None
    if (Path(cwd) / ".pre-commit-config.yaml").exists():
        if uv_bin and (Path(cwd) / "uv.lock").exists():
            hook_cmd = [uv_bin, "run", "pre-commit", "run", "--files"] + paths
        elif pre_commit_bin:
            hook_cmd = [pre_commit_bin, "run", "--files"] + paths
        else:
            return {
                "success": False,
                "error": "pre-commit config exists but no pre-commit binary found",
                "failure_class": CommitFailureClass.TOOLING_MISSING.value,
            }

    if hook_cmd is not None:
        uv_cache_dir = resolve_temp_dir(Path(cwd), workspace_temp_dir) / "uv-cache"
        uv_cache_dir.mkdir(parents=True, exist_ok=True)
        hook_env = build_sanitized_env()
        hook_env["UV_CACHE_DIR"] = str(uv_cache_dir)
        before_rc, before_stdout, _ = await _run_subprocess(
            ["git", "-C", cwd, "write-tree"], cwd=cwd, timeout=30
        )
        rc, stdout, stderr = await _run_subprocess(
            hook_cmd,
            cwd=cwd,
            timeout=120,
            env=hook_env,
        )
        if rc != 0:
            hook_output = _combined_process_output(
                stderr,
                stdout,
                f"pre-commit exited with status {rc}",
            )
            failure_class = _pre_commit_failure_class(hook_output)
            rc2, readd_stdout, readd_stderr = await _run_subprocess(
                ["git", "-C", cwd, "add", "--"] + paths, cwd=cwd, timeout=30
            )
            if rc2 != 0:
                readd_output = _combined_process_output(
                    readd_stderr,
                    readd_stdout,
                    f"git add exited with status {rc2}",
                )
                return {
                    "success": False,
                    "error": f"pre-commit re-add failed: {readd_output}",
                    "failure_class": CommitFailureClass.GIT_ADD_FAILED.value,
                }
            after_rc, after_stdout, _ = await _run_subprocess(
                ["git", "-C", cwd, "write-tree"], cwd=cwd, timeout=30
            )
            if (
                before_rc == 0
                and after_rc == 0
                and bool(before_stdout.strip())
                and before_stdout.strip() == after_stdout.strip()
            ):
                return {
                    "success": False,
                    "error": f"pre-commit failed: {hook_output}",
                    "failure_class": failure_class.value,
                    "retry_skipped_reason": "staged tree unchanged after pre-commit failure",
                }
            rc3, stdout3, stderr3 = await _run_subprocess(
                hook_cmd,
                cwd=cwd,
                timeout=120,
                env=hook_env,
            )
            if rc3 != 0:
                retry_output = _combined_process_output(
                    stderr3,
                    stdout3,
                    f"pre-commit retry exited with status {rc3}",
                )
                return {
                    "success": False,
                    "error": f"pre-commit retry failed: {retry_output}",
                    "failure_class": _pre_commit_failure_class(retry_output).value,
                }
    return None


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "headless"}, annotations={"readOnlyHint": True}
)
@_cancellation_shield()
# No _require_enabled() — headless skill sessions need test_check without opening kitchen.
# The kitchen tag governs visibility only; the headless tag provides access in SKILL sessions.
@track_response_size("test_check")
async def test_check(
    worktree_path: str,
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Run the configured test command in a worktree directory. Returns unambiguous PASS/FAIL.

    CRITICAL: This tool is a pipeline gate, not a diagnostic tool. When it
    returns {"passed": false}, follow the pipeline script's on_failure routing
    (e.g. call resolve-failures via run_skill). Do NOT:
    - Run tests yourself (pytest, make test, etc.) to investigate
    - Read test output or try to diagnose failures
    - Attempt to fix code directly
    The on_failure step handles all diagnosis and remediation.

    Args:
        worktree_path: Path to the git worktree to run tests in.
        step_name: Optional YAML step key for wall-clock timing accumulation.
            Has no effect on test filtering — filter behavior is controlled by
            config.test_check.filter_mode.

    Never raises.
    """
    prepared_segment: PreparedRecipeSegmentDelivery | None = None
    try:
        resolved = os.path.realpath(worktree_path)
        with structlog.contextvars.bound_contextvars(tool="test_check", cwd=resolved):
            logger.info("test_check", worktree=resolved)
            await _notify(
                ctx,
                "info",
                f"test_check: {resolved}",
                "autoskillit.test_check",
                extra={"worktree": resolved},
            )

            from autoskillit.server import _get_ctx  # circular-break

            tool_ctx = _get_ctx()
            prepared_segment = prepare_recipe_segment_delivery(tool_ctx, step_name)

            def _finish(response: dict[str, object], *, success: bool) -> str:
                wire_response = attach_recipe_segment(
                    response,
                    prepared_segment,
                    success=success,
                )
                try:
                    tool_ctx.workspace_outcome_ledger.record(
                        WorkspaceOutcomeRecord(
                            workspace=resolved,
                            recorded_at=datetime.now(UTC).isoformat(),
                            kind=WorkspaceOutcomeKind.TEST_RUN,
                            succeeded=response.get("passed") is True,
                            timed_out=response.get("timed_out") is True,
                            infrastructure_missing=(
                                response.get("infrastructure_missing") is True
                            ),
                        )
                    )
                except Exception as exc:
                    logger.error("test_check outcome recording failed", exc_info=True)
                    wire_response = attach_recipe_segment(
                        {
                            "passed": False,
                            "error": (
                                f"workspace outcome recording failed: {type(exc).__name__}: {exc}"
                            ),
                        },
                        prepared_segment,
                        success=False,
                    )
                return json.dumps(wire_response)

            if tool_ctx.tester is None:
                return _finish(
                    {"passed": False, "error": "Test runner not configured"},
                    success=False,
                )

            if not os.path.isdir(resolved):
                logger.warning("test_check path does not exist", path=resolved)
                return _finish(
                    {
                        "passed": False,
                        "error": f"Worktree path does not exist: {resolved}",
                        "infrastructure_missing": True,
                    },
                    success=False,
                )
            infra_issue = tool_ctx.tester.check_infrastructure(Path(resolved))
            if infra_issue is not None:
                logger.warning("test_check infrastructure missing", detail=infra_issue)
                return _finish(
                    {
                        "passed": False,
                        "error": f"Test infrastructure not found: {infra_issue}",
                        "infrastructure_missing": True,
                    },
                    success=False,
                )

            _start = time.monotonic()
            try:
                test_result = await tool_ctx.tester.run(Path(resolved))
                timed_out = test_result.outer_timeout_seconds is not None
                effective_passed = test_result.passed and not timed_out

                if not effective_passed:
                    await _notify(
                        ctx,
                        "error",
                        (
                            f"test_check: outer timeout after "
                            f"{test_result.outer_timeout_seconds:.1f}s"
                            if timed_out
                            else "test_check: tests failed"
                        ),
                        "autoskillit.test_check",
                        extra={"worktree": resolved},
                    )

                response = _build_test_check_response(
                    tool_ctx,
                    test_result,
                    resolved,
                    timed_out=timed_out,
                    effective_passed=effective_passed,
                )
                return _finish(response, success=effective_passed)
            except WorktreeGateContention as exc:
                return _finish({"passed": False, "error": str(exc)}, success=False)
            except Exception as exc:
                logger.error("test_check unhandled exception", exc_info=True)
                return _finish(
                    {"passed": False, "error": f"{type(exc).__name__}: {exc}"},
                    success=False,
                )
            finally:
                if step_name:
                    tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.error("test_check unhandled exception", exc_info=True)
        return json.dumps(
            attach_recipe_segment(
                {"passed": False, "error": f"{type(exc).__name__}: {exc}"},
                prepared_segment,
                success=False,
            )
        )


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "headless"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
@track_response_size("commit_files")
async def commit_files(
    paths: list[str],
    message: str,
    cwd: str,
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Stage and commit specified files in a worktree via the server process.

    Runs pre-commit hooks on the staged files before committing. If hooks
    auto-fix files, re-stages and retries the commit once. On hard hook
    failure, returns an error envelope without committing.

    Args:
        paths: List of file paths (relative to cwd) to stage and commit.
        message: Commit message.
        cwd: Absolute path to the git worktree.
        step_name: Optional YAML step key for wall-clock timing accumulation.

    Never raises.
    """
    try:
        resolved = os.path.realpath(cwd)
        with structlog.contextvars.bound_contextvars(tool="commit_files", cwd=resolved):
            logger.info("commit_files", path_count=len(paths), cwd=resolved)

            from autoskillit.server import _get_ctx  # circular-break

            tool_ctx = _get_ctx()

            def _finish(
                response: dict[str, object],
                *,
                failure_class: CommitFailureClass | None = None,
            ) -> str:
                if failure_class is not None:
                    response["failure_class"] = failure_class.value
                commit_sha = response.get("commit_sha")
                succeeded = response.get("success") is True
                try:
                    tool_ctx.workspace_outcome_ledger.record(
                        WorkspaceOutcomeRecord(
                            workspace=resolved,
                            recorded_at=datetime.now(UTC).isoformat(),
                            kind=WorkspaceOutcomeKind.COMMIT_ATTEMPT,
                            succeeded=succeeded,
                            commit_sha=commit_sha if isinstance(commit_sha, str) else None,
                            failure_class=None if succeeded else failure_class,
                        )
                    )
                except Exception as exc:
                    logger.error("commit_files outcome recording failed", exc_info=True)
                    ledger_failure: dict[str, object] = {
                        "success": False,
                        "error": (
                            f"workspace outcome recording failed: {type(exc).__name__}: {exc}"
                        ),
                        "failure_class": CommitFailureClass.UNHANDLED.value,
                    }
                    if isinstance(commit_sha, str) and commit_sha:
                        ledger_failure["commit_sha"] = commit_sha
                    return json.dumps(ledger_failure)
                return json.dumps(response)

            if not cwd or not os.path.isdir(resolved):
                return _finish(
                    {
                        "success": False,
                        "error": f"cwd does not exist or is not a directory: {resolved}",
                    },
                    failure_class=CommitFailureClass.PATH_REJECTED,
                )
            if not paths:
                return _finish(
                    {"success": False, "error": "paths list is empty"},
                    failure_class=CommitFailureClass.PATH_REJECTED,
                )

            from autoskillit.server.git import validate_commit_paths  # circular-break

            if (path_error := validate_commit_paths(resolved, paths)) is not None:
                return _finish(
                    {"success": False, "error": path_error},
                    failure_class=CommitFailureClass.PATH_REJECTED,
                )
            _start = time.monotonic()

            try:
                rc, stdout, stderr = await _run_subprocess(
                    ["git", "-C", resolved, "add", "--"] + paths,
                    cwd=resolved,
                    timeout=30,
                )
                if rc != 0:
                    return _finish(
                        {
                            "success": False,
                            "error": (
                                "git add failed: "
                                + _combined_process_output(
                                    stderr,
                                    stdout,
                                    f"git add exited with status {rc}",
                                )
                            ),
                        },
                        failure_class=CommitFailureClass.GIT_ADD_FAILED,
                    )

                if (
                    hook_error := await _run_pre_commit_transaction(
                        resolved,
                        paths,
                        workspace_temp_dir=tool_ctx.config.workspace.temp_dir,
                    )
                ) is not None:
                    hook_failure_class = CommitFailureClass(str(hook_error.pop("failure_class")))
                    return _finish(hook_error, failure_class=hook_failure_class)

                rc, stdout, stderr = await _run_subprocess(
                    ["git", "-C", resolved, "commit", "-m", message],
                    cwd=resolved,
                    timeout=30,
                )
                if rc != 0:
                    return _finish(
                        {
                            "success": False,
                            "error": (
                                "git commit failed: "
                                + _combined_process_output(
                                    stderr,
                                    stdout,
                                    f"git commit exited with status {rc}",
                                )
                            ),
                        },
                        failure_class=CommitFailureClass.GIT_COMMIT_FAILED,
                    )

                rc, stdout, stderr = await _run_subprocess(
                    ["git", "-C", resolved, "rev-parse", "HEAD"],
                    cwd=resolved,
                    timeout=10,
                )
                commit_sha = stdout.strip()
                if rc != 0 or not commit_sha:
                    return _finish(
                        {
                            "success": False,
                            "error": (
                                "git rev-parse HEAD failed: "
                                + _combined_process_output(
                                    stderr,
                                    stdout,
                                    f"git rev-parse exited with status {rc}",
                                )
                            ),
                        },
                        failure_class=CommitFailureClass.UNHANDLED,
                    )

                return _finish({"success": True, "commit_sha": commit_sha})
            except Exception as exc:
                logger.error("commit_files unhandled exception", exc_info=True)
                return _finish(
                    {"success": False, "error": f"{type(exc).__name__}: {exc}"},
                    failure_class=CommitFailureClass.UNHANDLED,
                )
            finally:
                if step_name:
                    tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.error("commit_files unhandled exception", exc_info=True)
        return json.dumps(
            {
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "failure_class": CommitFailureClass.UNHANDLED.value,
            }
        )


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("reset_test_dir")
async def reset_test_dir(
    test_dir: str,
    force: bool = False,
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Remove all files from a test directory. Only works on directories with a reset guard marker.

    The directory must contain the configured marker file (default: .autoskillit-workspace)
    unless force=True is set. Use ``autoskillit workspace init <dir>`` to create the marker.

    Args:
        test_dir: Path to the test directory to clear. Must contain the reset guard marker.
        force: Override the marker check. When True, all contents are deleted
               including the marker file itself.
        step_name: Optional YAML step key for wall-clock timing accumulation.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        resolved = os.path.realpath(test_dir)
        with structlog.contextvars.bound_contextvars(tool="reset_test_dir", cwd=resolved):
            logger.info("reset_test_dir", resolved=str(resolved), force=force)
            await _notify(
                ctx,
                "info",
                f"reset_test_dir: {resolved}",
                "autoskillit.reset_test_dir",
                extra={"resolved": resolved, "force": force},
            )

            from autoskillit.server import _get_config, _get_ctx  # circular-break

            tool_ctx = _get_ctx()
            _start = time.monotonic()
            try:
                if not os.path.isdir(resolved):
                    await _notify(
                        ctx,
                        "error",
                        "reset_test_dir failed",
                        "autoskillit.reset_test_dir",
                        extra={"reason": "directory does not exist"},
                    )
                    return json.dumps({"error": f"Directory does not exist: {resolved}"})

                marker_name = _get_config().safety.reset_guard_marker
                marker_path = Path(resolved) / marker_name
                if not force and not marker_path.is_file():
                    await _notify(
                        ctx,
                        "error",
                        "reset_test_dir failed",
                        "autoskillit.reset_test_dir",
                        extra={"reason": "marker missing"},
                    )
                    return json.dumps(
                        {
                            "error": (
                                f"Safety: directory missing reset guard marker ({marker_name})"
                            ),
                            "hint": (
                                f"Create the marker with: autoskillit workspace init {resolved}"
                            ),
                        }
                    )

                if tool_ctx.workspace_mgr is None:
                    return json.dumps({"error": "Workspace manager not configured"})

                preserve = None if force else {marker_name}
                cleanup = tool_ctx.workspace_mgr.delete_contents(Path(resolved), preserve=preserve)
                return json.dumps({**cleanup.to_dict(), "forced": force})
            finally:
                if step_name:
                    tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.error("reset_test_dir unhandled exception", exc_info=True)
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("reset_workspace")
async def reset_workspace(test_dir: str, ctx: Context = CurrentContext()) -> str:
    """Runs a configured reset command then deletes directory contents,
    preserving configured directories and the reset guard marker.

    Args:
        test_dir: Path to the test project directory. Must contain the reset guard marker.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        resolved = os.path.realpath(test_dir)
        with structlog.contextvars.bound_contextvars(tool="reset_workspace", cwd=resolved):
            logger.info("reset_workspace", resolved=str(resolved))
            await _notify(
                ctx,
                "info",
                f"reset_workspace: {resolved}",
                "autoskillit.reset_workspace",
                extra={"resolved": resolved},
            )

            if not os.path.isdir(resolved):
                await _notify(
                    ctx,
                    "error",
                    "reset_workspace failed",
                    "autoskillit.reset_workspace",
                    extra={"reason": "directory does not exist"},
                )
                return json.dumps({"error": f"Directory does not exist: {resolved}"})

            from autoskillit.server import _get_config  # circular-break

            marker_name = _get_config().safety.reset_guard_marker
            marker_path = Path(resolved) / marker_name
            if not marker_path.is_file():
                await _notify(
                    ctx,
                    "error",
                    "reset_workspace failed",
                    "autoskillit.reset_workspace",
                    extra={"reason": "marker missing"},
                )
                return json.dumps(
                    {
                        "error": f"Safety: directory missing reset guard marker ({marker_name})",
                        "hint": f"Create the marker with: autoskillit workspace init {resolved}",
                    }
                )

            reset_cmd = _get_config().reset_workspace.command
            if reset_cmd is None:
                await _notify(
                    ctx,
                    "error",
                    "reset_workspace failed",
                    "autoskillit.reset_workspace",
                    extra={"reason": "not configured"},
                )
                return json.dumps({"error": "reset_workspace not configured for this project"})

            returncode, stdout, stderr = await _run_subprocess(
                reset_cmd,
                cwd=resolved,
                timeout=60,
            )

            if returncode != 0:
                await _notify(
                    ctx,
                    "error",
                    "reset_workspace failed",
                    "autoskillit.reset_workspace",
                    extra={"reason": "reset command failed", "exit_code": returncode},
                )
                return json.dumps(
                    {
                        "error": "reset command failed",
                        "exit_code": returncode,
                        "stderr": truncate_text(stderr),
                    }
                )

            from autoskillit.server import _get_ctx  # circular-break

            tool_ctx = _get_ctx()
            if tool_ctx.workspace_mgr is None:
                return json.dumps({"error": "Workspace manager not configured"})

            preserve = set(_get_config().reset_workspace.preserve_dirs) | {marker_name}
            cleanup = tool_ctx.workspace_mgr.delete_contents(Path(resolved), preserve=preserve)
            return json.dumps(cleanup.to_dict())
    except Exception as exc:
        logger.error("reset_workspace unhandled exception", exc_info=True)
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
