#!/usr/bin/env python3
"""MCP server for orchestrating automated skill-driven workflows.

All tools are gated by default and require the user to type
/mcp__autoskillit__enable_tools to activate. This uses MCP prompts
(user-controlled, model cannot invoke) to set an in-memory flag
that each tool checks before executing. The gate survives
--dangerously-skip-permissions.

Transport: stdio (default for FastMCP).
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.prompts.prompt import Message, PromptResult

from autoskillit.config import AutomationConfig, load_config
from autoskillit.process_lifecycle import run_managed_async
from autoskillit.types import MergeFailedStep, MergeState, RestartScope, RetryReason

mcp = FastMCP("autoskillit")

_config: AutomationConfig = load_config(Path.cwd())

_plugin_dir = str(Path(__file__).parent)

_tools_enabled = False


def _version_info() -> dict:
    """Return version health information for the running server."""
    from autoskillit import __version__

    plugin_json_path = Path(_plugin_dir) / ".claude-plugin" / "plugin.json"
    plugin_version = None
    if plugin_json_path.is_file():
        data = json.loads(plugin_json_path.read_text())
        plugin_version = data.get("version")

    return {
        "package_version": __version__,
        "plugin_json_version": plugin_version,
        "match": __version__ == plugin_version,
    }


def _require_enabled() -> str | None:
    """Return error JSON if tools are not enabled, None if OK.

    All tools are gated by default and can only be activated by the user
    typing /mcp__autoskillit__enable_tools. This survives
    --dangerously-skip-permissions because MCP prompts are outside
    the permission system.
    """
    if not _tools_enabled:
        return json.dumps(
            {
                "error": (
                    "AutoSkillit tools are not enabled. "
                    "User must type /mcp__autoskillit__enable_tools to activate."
                ),
            }
        )
    return None


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


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
    result = await run_managed_async(cmd, cwd=Path(cwd), timeout=timeout)
    if result.timed_out:
        return -1, result.stdout, f"Process timed out after {timeout}s"
    return result.returncode, result.stdout, result.stderr


def _check_dry_walkthrough(skill_command: str, cwd: str) -> str | None:
    """If skill_command is an implement skill, verify the plan has been dry-walked.

    Returns an error JSON string if validation fails, None if OK.
    """
    parts = skill_command.strip().split(None, 1)
    if not parts or parts[0] not in _config.implement_gate.skill_names:
        return None

    skill_name = parts[0]

    if len(parts) < 2:
        return json.dumps({"error": f"Missing plan path argument for {skill_name}"})

    plan_path = Path(cwd) / parts[1].strip().strip('"').strip("'")
    if not plan_path.is_file():
        return json.dumps({"error": f"Plan file not found: {plan_path}"})

    first_line = plan_path.read_text().split("\n", 1)[0].strip()
    if first_line != _config.implement_gate.marker:
        return json.dumps(
            {
                "error": "Plan has NOT been dry-walked. Run /dry-walkthrough on the plan first.",
                "plan_path": str(plan_path),
                "expected_first_line": _config.implement_gate.marker,
                "actual_first_line": first_line[:100],
            }
        )

    return None


def _truncate(text: str, max_len: int = 5000) -> str:
    if len(text) <= max_len:
        return text
    return f"...[truncated {len(text) - max_len} chars]...\n" + text[-max_len:]


@dataclass
class ClaudeSessionResult:
    """Parsed result from a Claude Code headless session."""

    subtype: str  # "success", "error_max_turns", "error_during_execution", etc.
    is_error: bool
    result: str
    session_id: str
    errors: list[str] = field(default_factory=list)

    def _is_context_exhausted(self) -> bool:
        """Detect context window exhaustion from Claude's error output."""
        return self.is_error and "prompt is too long" in self.result.lower()

    @property
    def needs_retry(self) -> bool:
        """Whether the session didn't finish and should be retried."""
        if self.subtype == "error_max_turns":
            return True
        if self._is_context_exhausted():
            return True
        return False

    @property
    def retry_reason(self) -> RetryReason:
        """Why retry is needed. NONE if needs_retry is False."""
        if self.needs_retry:
            return RetryReason.RESUME
        return RetryReason.NONE


def parse_session_result(stdout: str) -> ClaudeSessionResult:
    """Parse Claude Code's --output-format json stdout into a typed result.

    Handles multi-line NDJSON (Claude Code may emit multiple JSON objects;
    the last 'result' type object is authoritative).
    Falls back gracefully for non-JSON or missing fields.
    """
    if not stdout.strip():
        return ClaudeSessionResult(
            subtype="unknown",
            is_error=False,
            result="",
            session_id="",
            errors=[],
        )

    result_obj = None
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("type") == "result":
                result_obj = obj
        except json.JSONDecodeError:
            continue

    if result_obj is None:
        try:
            result_obj = json.loads(stdout)
        except json.JSONDecodeError:
            return ClaudeSessionResult(
                subtype="unknown",
                is_error=False,
                result=stdout,
                session_id="",
                errors=[],
            )

    return ClaudeSessionResult(
        subtype=result_obj.get("subtype", "unknown"),
        is_error=result_obj.get("is_error", False),
        result=result_obj.get("result", ""),
        session_id=result_obj.get("session_id", ""),
        errors=result_obj.get("errors", []),
    )


@dataclass
class CleanupResult:
    deleted: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.failed) == 0

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "deleted": self.deleted,
            "failed": [{"path": p, "error": e} for p, e in self.failed],
            "skipped": self.skipped,
        }


def _delete_directory_contents(
    directory: Path,
    preserve: set[str] | None = None,
) -> CleanupResult:
    """Delete all items in directory, skipping preserved names.

    Never raises. All errors captured in CleanupResult.failed.
    FileNotFoundError treated as success (item already gone).
    """
    result = CleanupResult()
    for item_name in os.listdir(directory):
        if preserve and item_name in preserve:
            result.skipped.append(item_name)
            continue
        path = directory / item_name
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            result.deleted.append(item_name)
        except FileNotFoundError:
            result.deleted.append(item_name)  # gone = success
        except OSError as exc:
            result.failed.append((item_name, f"{type(exc).__name__}: {exc}"))
    return result


@mcp.tool(tags={"automation"})
async def run_cmd(cmd: str, cwd: str, timeout: int = 600) -> str:
    """Run an arbitrary shell command in the specified directory.

    Args:
        cmd: The full command to run (e.g. "make build").
        cwd: Working directory for the command.
        timeout: Max seconds before killing the process (default 600).
    """
    if (gate := _require_enabled()) is not None:
        return gate
    _log(f"run_cmd: cmd={cmd[:80]}... cwd={cwd}")
    returncode, stdout, stderr = await _run_subprocess(
        ["bash", "-c", cmd],
        cwd=cwd,
        timeout=float(timeout),
    )
    return json.dumps(
        {
            "success": returncode == 0,
            "exit_code": returncode,
            "stdout": _truncate(stdout),
            "stderr": _truncate(stderr),
        }
    )


async def _import_and_call(
    dotted_path: str,
    args: dict[str, object] | None = None,
    timeout: float = 30,
) -> dict[str, object]:
    """Import a Python callable by dotted path and invoke it.

    Returns dict with 'success', 'result' (or 'error').
    Handles sync and async callables, with timeout protection.
    """
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
        return {"success": False, "error": f"Timeout after {timeout}s calling {dotted_path}"}
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        json.dumps(result)
        return {"success": True, "result": result}
    except (TypeError, ValueError):
        return {"success": True, "result": str(result)}


@mcp.tool(tags={"automation"})
async def run_python(
    callable: str, args: dict[str, object] | None = None, timeout: int = 30
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
    """
    if (gate := _require_enabled()) is not None:
        return gate
    _log(f"run_python: callable={callable} timeout={timeout}")
    result = await _import_and_call(callable, args=args, timeout=float(timeout))
    return json.dumps(result)


@mcp.tool(tags={"automation"})
async def run_skill(skill_command: str, cwd: str, add_dir: str = "") -> str:
    """Run a Claude Code headless session with a skill command.

    Args:
        skill_command: The full prompt including skill invocation (e.g. "/investigate ...").
        cwd: Working directory for the claude session.
        add_dir: Optional additional directory to add to the session context.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    _log(f"run_skill: command={skill_command[:80]}... cwd={cwd}")

    if _config.safety.require_dry_walkthrough:
        if (gate_error := _check_dry_walkthrough(skill_command, cwd)) is not None:
            return gate_error

    cmd = [
        "claude",
        "-p",
        skill_command,
        "--plugin-dir",
        _plugin_dir,
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
    ]
    if add_dir:
        cmd.extend(["--add-dir", add_dir])

    returncode, stdout, stderr = await _run_subprocess(cmd, cwd=cwd, timeout=3600)

    session = parse_session_result(stdout)
    return json.dumps(
        {
            "result": _truncate(session.result),
            "session_id": session.session_id,
            "subtype": session.subtype,
            "is_error": session.is_error,
            "exit_code": returncode,
        }
    )


@mcp.tool(tags={"automation"})
async def run_skill_retry(skill_command: str, cwd: str) -> str:
    """Run a Claude Code headless session with retry detection.

    Use this for long-running skill sessions that may hit the context limit.
    The needs_retry field indicates whether the session didn't finish.
    When needs_retry is true, retry_reason is "resume" — the session should
    be retried to continue from where it left off.

    Args:
        skill_command: The full prompt including skill invocation.
        cwd: Working directory for the claude session.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    _log(f"run_skill_retry: command={skill_command[:80]}...")

    if _config.safety.require_dry_walkthrough:
        if (gate_error := _check_dry_walkthrough(skill_command, cwd)) is not None:
            return gate_error

    cmd = [
        "claude",
        "-p",
        skill_command,
        "--plugin-dir",
        _plugin_dir,
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
    ]

    returncode, stdout, stderr = await _run_subprocess(cmd, cwd=cwd, timeout=7200)

    session = parse_session_result(stdout)
    return json.dumps(
        {
            "result": _truncate(session.result),
            "session_id": session.session_id,
            "subtype": session.subtype,
            "is_error": session.is_error,
            "exit_code": returncode,
            "needs_retry": session.needs_retry,
            "retry_reason": session.retry_reason,
        }
    )


_OUTCOME_PATTERN = re.compile(
    r"(\d+)\s+(passed|failed|error|xfailed|xpassed|skipped|warnings?|deselected)"
)


def _parse_pytest_summary(stdout: str) -> dict[str, int]:
    """Extract pytest outcome counts from the last summary-like line.

    Scans stdout in reverse for a line containing recognizable pytest
    outcome words (e.g. "passed", "failed") and extracts all "N outcome"
    pairs into a dict. Returns empty dict if no summary line found.
    """
    for line in reversed(stdout.splitlines()):
        matches = _OUTCOME_PATTERN.findall(line)
        if matches:
            counts: dict[str, int] = {}
            for count_str, outcome in matches:
                key = outcome.rstrip("s") if outcome == "warnings" else outcome
                counts[key] = int(count_str)
            return counts
    return {}


def _check_test_passed(returncode: int, stdout: str) -> bool:
    """Determine test pass/fail with cross-validation.

    Uses exit code as primary signal, but overrides to False if the
    output contains failure indicators — defense against exit code bugs
    in external tools (e.g. Taskfile PIPESTATUS in non-bash shell).
    """
    if returncode != 0:
        return False
    counts = _parse_pytest_summary(stdout)
    if counts.get("failed", 0) > 0 or counts.get("error", 0) > 0:
        return False
    return True


@mcp.tool(tags={"automation"})
async def test_check(worktree_path: str) -> str:
    """Run the configured test command in a worktree directory. Returns unambiguous PASS/FAIL.

    CRITICAL: This tool is a pipeline gate, not a diagnostic tool. When it
    returns {"passed": false}, follow the pipeline script's on_failure routing
    (e.g. call assess-and-merge via run_skill). Do NOT:
    - Run tests yourself (pytest, make test, etc.) to investigate
    - Read test output or try to diagnose failures
    - Attempt to fix code directly
    The on_failure step handles all diagnosis and remediation.

    Args:
        worktree_path: Path to the git worktree to run tests in.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    _log(f"test_check: worktree={worktree_path}")
    returncode, stdout, stderr = await _run_subprocess(
        _config.test_check.command,
        cwd=worktree_path,
        timeout=_config.test_check.timeout,
    )

    passed = _check_test_passed(returncode, stdout)

    return json.dumps({"passed": passed})


@mcp.tool(tags={"automation"})
async def merge_worktree(worktree_path: str, base_branch: str) -> str:
    """Merge a worktree branch into the base branch after verifying tests pass.

    Programmatic gate: runs the configured test command in the worktree before allowing merge.
    If tests fail, returns error without merging.

    Args:
        worktree_path: Absolute path to the git worktree.
        base_branch: Branch to merge into (e.g. "main").
    """
    if (gate := _require_enabled()) is not None:
        return gate
    _log(f"merge_worktree: path={worktree_path} base={base_branch}")

    # Validate worktree path exists
    if not os.path.isdir(worktree_path):
        return json.dumps({"error": f"Path does not exist: {worktree_path}"})

    # Verify it's a git worktree
    rc, git_dir, stderr = await _run_subprocess(
        ["git", "rev-parse", "--git-dir"],
        cwd=worktree_path,
        timeout=10,
    )
    if rc != 0 or "/worktrees/" not in git_dir:
        return json.dumps({"error": f"Not a git worktree: {worktree_path}", "stderr": stderr})

    # Get branch name
    rc, branch_out, stderr = await _run_subprocess(
        ["git", "branch", "--show-current"],
        cwd=worktree_path,
        timeout=10,
    )
    if rc != 0:
        return json.dumps({"error": f"Could not determine branch: {stderr}"})
    worktree_branch = branch_out.strip()

    # Test gate
    if _config.safety.test_gate_on_merge:
        rc, test_stdout, test_stderr = await _run_subprocess(
            _config.test_check.command,
            cwd=worktree_path,
            timeout=_config.test_check.timeout,
        )
        if not _check_test_passed(rc, test_stdout):
            return json.dumps(
                {
                    "error": "Tests failed in worktree — merge blocked",
                    "failed_step": MergeFailedStep.TEST_GATE,
                    "state": MergeState.WORKTREE_INTACT,
                    "worktree_path": worktree_path,
                }
            )

    # Rebase
    fetch_rc, _, fetch_stderr = await _run_subprocess(
        ["git", "fetch", "origin"],
        cwd=worktree_path,
        timeout=60,
    )
    if fetch_rc != 0:
        return json.dumps(
            {
                "error": "git fetch origin failed",
                "failed_step": MergeFailedStep.FETCH,
                "state": MergeState.WORKTREE_INTACT,
                "stderr": _truncate(fetch_stderr),
                "worktree_path": worktree_path,
            }
        )

    rc, _, rebase_stderr = await _run_subprocess(
        ["git", "rebase", f"origin/{base_branch}"],
        cwd=worktree_path,
        timeout=120,
    )
    if rc != 0:
        await _run_subprocess(
            ["git", "rebase", "--abort"],
            cwd=worktree_path,
            timeout=30,
        )
        return json.dumps(
            {
                "error": "Rebase failed — aborted to clean state",
                "failed_step": MergeFailedStep.REBASE,
                "state": MergeState.WORKTREE_INTACT_REBASE_ABORTED,
                "stderr": rebase_stderr,
                "worktree_path": worktree_path,
            }
        )

    # Determine main repo path from worktree list
    rc, wt_list, _ = await _run_subprocess(
        ["git", "worktree", "list", "--porcelain"],
        cwd=worktree_path,
        timeout=10,
    )
    main_repo = ""
    for line in wt_list.splitlines():
        if line.startswith("worktree "):
            main_repo = line.split(" ", 1)[1].strip()
            break  # First entry is always the main working tree

    if not main_repo:
        return json.dumps({"error": "Could not determine main repo path from worktree list"})

    # Merge from main repo
    rc, _, merge_stderr = await _run_subprocess(
        ["git", "merge", worktree_branch],
        cwd=main_repo,
        timeout=60,
    )
    if rc != 0:
        await _run_subprocess(
            ["git", "merge", "--abort"],
            cwd=main_repo,
            timeout=30,
        )
        return json.dumps(
            {
                "error": "Merge failed — aborted to clean state",
                "failed_step": MergeFailedStep.MERGE,
                "state": MergeState.MAIN_REPO_MERGE_ABORTED,
                "stderr": merge_stderr,
                "worktree_path": worktree_path,
            }
        )

    # Cleanup
    wt_rc, _, wt_stderr = await _run_subprocess(
        ["git", "worktree", "remove", worktree_path],
        cwd=main_repo,
        timeout=30,
    )
    br_rc, _, br_stderr = await _run_subprocess(
        ["git", "branch", "-D", worktree_branch],
        cwd=main_repo,
        timeout=10,
    )

    return json.dumps(
        {
            "success": True,
            "merged_branch": worktree_branch,
            "into_branch": base_branch,
            "worktree_removed": wt_rc == 0,
            "branch_deleted": br_rc == 0,
        }
    )


@mcp.tool(tags={"automation"})
async def reset_test_dir(test_dir: str, force: bool = False) -> str:
    """Remove all files from a test directory. Only works on directories with a reset guard marker.

    The directory must contain the configured marker file (default: .autoskillit-workspace)
    unless force=True is set. Use ``autoskillit workspace init <dir>`` to create the marker.

    Args:
        test_dir: Path to the test directory to clear. Must contain the reset guard marker.
        force: Override the marker check. When True, all contents are deleted
               including the marker file itself.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    resolved = os.path.realpath(test_dir)
    _log(f"reset_test_dir: resolved={resolved} force={force}")

    if not os.path.isdir(resolved):
        return json.dumps({"error": f"Directory does not exist: {resolved}"})

    marker_name = _config.safety.reset_guard_marker
    marker_path = Path(resolved) / marker_name
    if not force and not marker_path.is_file():
        return json.dumps(
            {
                "error": f"Safety: directory missing reset guard marker ({marker_name})",
                "hint": f"Create the marker with: autoskillit workspace init {resolved}",
            }
        )

    preserve = None if force else {marker_name}
    cleanup = _delete_directory_contents(Path(resolved), preserve=preserve)
    return json.dumps({**cleanup.to_dict(), "forced": force})


@mcp.tool(tags={"automation"})
async def classify_fix(worktree_path: str, base_branch: str) -> str:
    """Analyze a worktree's changes to determine if the fix requires restarting
    from plan creation or just re-running the implementation.

    Inspects git diff between the worktree HEAD and the base branch merge-base.
    If any changed files are in critical paths, returns full_restart.
    Otherwise returns partial_restart.

    Args:
        worktree_path: Path to the git worktree with the implemented fix.
        base_branch: The branch the worktree was created from (for merge-base).
    """
    if (gate := _require_enabled()) is not None:
        return gate
    _log(f"classify_fix: worktree={worktree_path} base={base_branch}")

    returncode, stdout, stderr = await _run_subprocess(
        ["git", "diff", "--name-only", f"origin/{base_branch}...HEAD"],
        cwd=worktree_path,
        timeout=30,
    )

    if returncode != 0:
        return json.dumps({"error": f"git diff failed: {stderr}"})

    changed_files = [f.strip() for f in stdout.splitlines() if f.strip()]

    prefixes = _config.classify_fix.path_prefixes
    critical_files = [f for f in changed_files if any(f.startswith(prefix) for prefix in prefixes)]

    if critical_files:
        return json.dumps(
            {
                "restart_scope": RestartScope.FULL_RESTART,
                "reason": f"Fix touches critical paths: {', '.join(critical_files[:5])}",
                "critical_files": critical_files,
                "all_changed_files": changed_files,
            }
        )

    return json.dumps(
        {
            "restart_scope": RestartScope.PARTIAL_RESTART,
            "reason": "Fix does not touch critical paths — partial restart is sufficient",
            "critical_files": [],
            "all_changed_files": changed_files,
        }
    )


@mcp.tool(tags={"automation"})
async def reset_workspace(test_dir: str) -> str:
    """Runs a configured reset command then deletes directory contents,
    preserving configured directories and the reset guard marker.

    Args:
        test_dir: Path to the test project directory. Must contain the reset guard marker.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    resolved = os.path.realpath(test_dir)
    _log(f"reset_workspace: resolved={resolved}")

    if not os.path.isdir(resolved):
        return json.dumps({"error": f"Directory does not exist: {resolved}"})

    marker_name = _config.safety.reset_guard_marker
    marker_path = Path(resolved) / marker_name
    if not marker_path.is_file():
        return json.dumps(
            {
                "error": f"Safety: directory missing reset guard marker ({marker_name})",
                "hint": f"Create the marker with: autoskillit workspace init {resolved}",
            }
        )

    if _config.reset_workspace.command is None:
        return json.dumps({"error": "reset_workspace not configured for this project"})

    returncode, stdout, stderr = await _run_subprocess(
        _config.reset_workspace.command,
        cwd=resolved,
        timeout=60,
    )

    if returncode != 0:
        return json.dumps(
            {
                "error": "reset command failed",
                "exit_code": returncode,
                "stderr": _truncate(stderr),
            }
        )

    preserve = set(_config.reset_workspace.preserve_dirs) | {marker_name}
    cleanup = _delete_directory_contents(Path(resolved), preserve=preserve)
    return json.dumps(cleanup.to_dict())


@mcp.tool(tags={"automation"})
async def autoskillit_status() -> str:
    """Return version health and configuration status for the running server.

    Reports package version, plugin.json version, version match status,
    tools enabled state, and active configuration summary. Call this after
    enabling tools or anytime you need to verify the server is healthy.

    This tool is always available (not gated by enable_tools).
    """
    info = _version_info()
    status = {
        "package_version": info["package_version"],
        "plugin_json_version": info["plugin_json_version"],
        "versions_match": info["match"],
        "tools_enabled": _tools_enabled,
    }
    if not info["match"]:
        status["warning"] = (
            f"Version mismatch: package is {info['package_version']} but "
            f"plugin.json reports {info['plugin_json_version']}. "
            f"Run `autoskillit doctor` for details or "
            f"`autoskillit install` to refresh the plugin cache."
        )
    return json.dumps(status)


@mcp.tool(tags={"automation"})
async def list_skill_scripts() -> str:
    """List available pipeline scripts from .autoskillit/scripts/.

    Returns a JSON array of scripts with name, description, and summary.
    Scripts are YAML workflow definitions that agents follow as orchestration
    instructions. Use load_skill_script to load a specific script.

    IMPORTANT: Pipeline scripts are NOT slash commands. They cannot be invoked
    as /autoskillit:<name>. They are loaded via load_skill_script and executed
    step-by-step by the agent. Scripts live in .autoskillit/scripts/ (NOT in
    .autoskillit/skills/ or any other directory).

    This tool is always available (not gated by enable_tools).
    """
    from autoskillit.script_loader import list_scripts

    result = list_scripts(Path.cwd())
    response: dict[str, object] = {
        "scripts": [
            {"name": s.name, "description": s.description, "summary": s.summary}
            for s in result.items
        ],
    }
    if result.errors:
        response["errors"] = [{"file": e.path.name, "error": e.error} for e in result.errors]
    return json.dumps(response)


@mcp.tool(tags={"automation"})
async def load_skill_script(name: str) -> str:
    """Load a pipeline script by name and return its raw YAML content.

    The YAML follows the workflow schema (inputs, steps with tool/action,
    on_success/on_failure routing, retry blocks). The agent should interpret
    the YAML and execute the steps using the appropriate MCP tools.

    After loading:
    1. Present the script to the user for review
    2. If the user requests changes, apply them, then ask whether to:
       - Save changes to the original file
       - Save as a new script (prompt for name)
       - Use temporarily without saving
    3. Prompt for input values using AskUserQuestion
    4. Execute the pipeline steps by calling MCP tools directly

    EXECUTION RULES:
    - Call MCP tools (run_skill, run_skill_retry, test_check, merge_worktree,
      etc.) DIRECTLY. Do NOT delegate pipeline execution to subagents.
    - The MCP tools themselves spawn headless sessions — no extra wrapping needed.
    - For parallel pipelines, call multiple MCP tools in parallel directly.
    - Thread outputs from each step into the next (e.g. worktree_path from
      implement into test_check).

    ROUTING RULES — MANDATORY:
    - When a tool returns a failure result (e.g. test_check returns
      {"passed": false}), you MUST follow the step's on_failure route.
    - Do NOT investigate, diagnose, or attempt to fix failures yourself.
    - Do NOT run shell commands (pytest, git diff, etc.) to understand
      what went wrong — the on_failure step handles all remediation.
    - Your ONLY job is to route to the correct next step and pass the
      required arguments. The downstream skill does the actual work.

    IMPORTANT: Pipeline scripts are NOT slash commands. They cannot be invoked
    as /autoskillit:<name>. The correct way to run a script is to call this
    tool, then follow the YAML steps. Scripts live in .autoskillit/scripts/
    as .yaml files (NOT in .autoskillit/skills/ or any other directory).

    This tool is always available (not gated by enable_tools).
    """
    from autoskillit.script_loader import load_script

    content = load_script(Path.cwd(), name)
    if content is None:
        return json.dumps({"error": f"No script named '{name}' in .autoskillit/scripts/"})
    return content


@mcp.tool(tags={"automation"})
async def validate_script(script_path: str) -> str:
    """Validate a pipeline script YAML file against the workflow schema.

    Parses the file, checks all validation rules (name, steps, routing,
    retry fields, input references), and returns structured results.
    Use after generating or editing a script to confirm it is valid.

    This tool is always available (not gated by enable_tools).

    Args:
        script_path: Absolute path to the .yaml script file to validate.
    """
    import yaml

    from autoskillit.workflow_loader import _parse_workflow, validate_workflow

    path = Path(script_path)
    if not path.is_file():
        return json.dumps({"error": f"File not found: {script_path}"})

    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        return json.dumps({"error": f"YAML parse error: {exc}"})

    if not isinstance(data, dict):
        return json.dumps({"error": "File must contain a YAML mapping"})

    wf = _parse_workflow(data)
    errors = validate_workflow(wf)

    if errors:
        return json.dumps({"valid": False, "errors": errors})
    return json.dumps({"valid": True, "errors": []})


def _enable_tools_handler() -> None:
    """Set the tools-enabled flag. Extracted for testability."""
    global _tools_enabled
    _tools_enabled = True


def _disable_tools_handler() -> None:
    """Clear the tools-enabled flag. Extracted for testability."""
    global _tools_enabled
    _tools_enabled = False


@mcp.resource("workflow://{name}")
def get_workflow(name: str) -> str:
    """Return workflow YAML for the orchestrating agent to follow."""
    from autoskillit.workflow_loader import list_workflows

    result = list_workflows(Path.cwd())
    match = next((w for w in result.items if w.name == name), None)
    if match is None:
        return json.dumps({"error": f"No workflow named '{name}'."})
    return match.path.read_text()


@mcp.prompt()
def enable_tools() -> PromptResult:
    """Enable all AutoSkillit tools for this session.

    Tools are disabled by default to prevent accidental use by agents.
    Only a human can invoke this prompt — the model cannot.
    This survives --dangerously-skip-permissions.

    Type /mcp__autoskillit__enable_tools to activate.
    """
    _enable_tools_handler()

    text = (
        "AutoSkillit tools are now enabled for this session. "
        "Call the autoskillit_status tool now to display version "
        "and health information to the user."
    )

    return PromptResult([Message(text, role="user")])


@mcp.prompt()
def disable_tools() -> PromptResult:
    """Disable all AutoSkillit tools for this session.

    Type /mcp__autoskillit__disable_tools to deactivate.
    """
    _disable_tools_handler()
    return PromptResult([Message("AutoSkillit tools are now disabled.", role="assistant")])
