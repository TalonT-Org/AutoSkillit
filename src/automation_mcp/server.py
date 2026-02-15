#!/usr/bin/env python3
"""MCP server for orchestrating automated bug-fix loops.

Eight tools expose command execution, skill invocation, test checking, worktree
merging, fix classification, and directory reset to an interactive Claude Code
session acting as the orchestrator.

Transport: stdio (default for FastMCP).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from automation_mcp.process_lifecycle import run_managed_async

mcp = FastMCP("bugfix-loop")


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


DRY_WALKTHROUGH_MARKER = "Dry-walkthrough verified = TRUE"


IMPLEMENT_SKILLS = {"/implement-worktree", "/implement-worktree-no-merge"}


def _check_dry_walkthrough(skill_command: str, cwd: str) -> str | None:
    """If skill_command is an implement skill, verify the plan has been dry-walked.

    Returns an error JSON string if validation fails, None if OK.
    """
    parts = skill_command.strip().split(None, 1)
    if not parts or parts[0] not in IMPLEMENT_SKILLS:
        return None

    skill_name = parts[0]

    if len(parts) < 2:
        return json.dumps({"error": f"Missing plan path argument for {skill_name}"})

    plan_path = Path(cwd) / parts[1].strip().strip('"').strip("'")
    if not plan_path.is_file():
        return json.dumps({"error": f"Plan file not found: {plan_path}"})

    first_line = plan_path.read_text().split("\n", 1)[0].strip()
    if first_line != DRY_WALKTHROUGH_MARKER:
        return json.dumps(
            {
                "error": "Plan has NOT been dry-walked. Run /dry-walkthrough on the plan first.",
                "plan_path": str(plan_path),
                "expected_first_line": DRY_WALKTHROUGH_MARKER,
                "actual_first_line": first_line[:100],
            }
        )

    return None


def _truncate(text: str, max_len: int = 5000) -> str:
    if len(text) <= max_len:
        return text
    return f"...[truncated {len(text) - max_len} chars]...\n" + text[-max_len:]


@mcp.tool()
async def run_cmd(cmd: str, cwd: str, timeout: int = 600) -> str:
    """Run an arbitrary shell command in the specified directory.

    Args:
        cmd: The full command to run (e.g. "planner create -t 'my task' --debug").
        cwd: Working directory for the command.
        timeout: Max seconds before killing the process (default 600).
    """
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


@mcp.tool()
async def run_skill(skill_command: str, cwd: str, add_dir: str = "") -> str:
    """Run a Claude Code headless session with a skill command (no turn limit).

    Args:
        skill_command: The full prompt including skill invocation (e.g. "/investigate ...").
        cwd: Working directory for the claude session.
        add_dir: Optional additional directory to add to the session context.
    """
    _log(f"run_skill: command={skill_command[:80]}... cwd={cwd}")
    cmd = [
        "claude",
        "-p",
        skill_command,
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
    ]
    if add_dir:
        cmd.extend(["--add-dir", add_dir])

    returncode, stdout, stderr = await _run_subprocess(cmd, cwd=cwd, timeout=3600)

    output: dict = {}
    if stdout:
        try:
            output = json.loads(stdout)
        except json.JSONDecodeError:
            output = {"result": stdout}

    return json.dumps(
        {
            "result": _truncate(output.get("result", "")),
            "session_id": output.get("session_id", ""),
            "exit_code": returncode,
        }
    )


_MAX_API_CALLS = 200


@mcp.tool()
async def run_skill_retry(skill_command: str, cwd: str) -> str:
    """Run a Claude Code headless session with an API call limit.

    Use this for /implement-worktree and /retry-worktree where context exhaustion
    is expected. The hit_api_limit field indicates whether to continue with
    /retry-worktree.

    Args:
        skill_command: The full prompt including skill invocation.
        cwd: Working directory for the claude session.
    """
    _log(f"run_skill_retry: command={skill_command[:80]}...")

    gate_error = _check_dry_walkthrough(skill_command, cwd)
    if gate_error is not None:
        return gate_error

    cmd = [
        "claude",
        "-p",
        skill_command,
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
        "--max-turns",
        str(_MAX_API_CALLS),
    ]

    returncode, stdout, stderr = await _run_subprocess(cmd, cwd=cwd, timeout=7200)

    output: dict = {}
    if stdout:
        try:
            output = json.loads(stdout)
        except json.JSONDecodeError:
            output = {"result": stdout}

    hit_api_limit = returncode != 0 and bool(re.search(r"\bturn", stderr, re.IGNORECASE))

    return json.dumps(
        {
            "result": _truncate(output.get("result", "")),
            "session_id": output.get("session_id", ""),
            "exit_code": returncode,
            "hit_api_limit": hit_api_limit,
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


@mcp.tool()
async def test_check(worktree_path: str) -> str:
    """Run task test-check in a worktree directory. Returns unambiguous PASS/FAIL.

    Args:
        worktree_path: Path to the git worktree to run tests in.
    """
    _log(f"test_check: worktree={worktree_path}")
    returncode, stdout, stderr = await _run_subprocess(
        ["task", "test-check"],
        cwd=worktree_path,
        timeout=600,
    )

    passed = _check_test_passed(returncode, stdout)

    return json.dumps({"passed": passed})


@mcp.tool()
async def merge_worktree(worktree_path: str, base_branch: str) -> str:
    """Merge a worktree branch into the base branch after verifying tests pass.

    Programmatic gate: runs task test-check in the worktree before allowing merge.
    If tests fail, returns error without merging.

    Args:
        worktree_path: Absolute path to the git worktree.
        base_branch: Branch to merge into (e.g. "main").
    """
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

    # Test gate — always runs, no bypass
    rc, test_stdout, test_stderr = await _run_subprocess(
        ["task", "test-check"],
        cwd=worktree_path,
        timeout=600,
    )
    if not _check_test_passed(rc, test_stdout):
        return json.dumps(
            {
                "error": "Tests failed in worktree — merge blocked",
                "failed_step": "test_gate",
                "state": "worktree_intact",
                "worktree_path": worktree_path,
            }
        )

    # Rebase
    await _run_subprocess(
        ["git", "fetch", "origin"],
        cwd=worktree_path,
        timeout=60,
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
                "failed_step": "rebase",
                "state": "worktree_intact_rebase_aborted",
                "stderr": rebase_stderr,
                "worktree_path": worktree_path,
            }
        )

    # Check if rebase changed anything (for potential re-test)
    rc, diff_out, _ = await _run_subprocess(
        ["git", "diff", "HEAD@{1}..HEAD"],
        cwd=worktree_path,
        timeout=30,
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
                "failed_step": "merge",
                "state": "main_repo_merge_aborted",
                "stderr": merge_stderr,
                "worktree_path": worktree_path,
            }
        )

    # Cleanup
    await _run_subprocess(
        ["git", "worktree", "remove", worktree_path],
        cwd=main_repo,
        timeout=30,
    )
    await _run_subprocess(
        ["git", "branch", "-D", worktree_branch],
        cwd=main_repo,
        timeout=10,
    )

    return json.dumps(
        {
            "success": True,
            "merged_branch": worktree_branch,
            "into_branch": base_branch,
            "worktree_removed": True,
        }
    )


PROJECT_MARKERS = [".claude", "CLAUDE.md", ".git", "pyproject.toml", "package.json"]


@mcp.tool()
async def reset_test_dir(test_dir: str, force: bool = False) -> str:
    """Remove all files from a test directory. Only works on playground directories.

    Refuses to wipe directories containing project markers (.claude, .git, etc.)
    unless force=True is explicitly set.

    Args:
        test_dir: Path to the test directory to clear. Must contain 'playground' in path.
        force: Override project marker safety check. Required if the directory
               contains .claude, .git, pyproject.toml, or package.json.
    """
    resolved = os.path.realpath(test_dir)
    _log(f"reset_test_dir: resolved={resolved} force={force}")

    if "playground" not in resolved:
        return json.dumps({"error": "Safety: only playground directories allowed"})

    if not os.path.isdir(resolved):
        return json.dumps({"error": f"Directory does not exist: {resolved}"})

    found_markers = [m for m in PROJECT_MARKERS if os.path.exists(os.path.join(resolved, m))]
    if found_markers and not force:
        return json.dumps(
            {
                "error": "Safety: directory contains project markers",
                "markers_found": found_markers,
                "hint": (
                    "Set force=True to override."
                    " This looks like a real project, not a scratch directory."
                ),
            }
        )

    for item in os.listdir(resolved):
        path = os.path.join(resolved, item)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)

    return json.dumps({"success": True, "forced": force, "markers_cleared": found_markers})


PLANNER_PATH_PREFIXES = [
    "agents/graph/planner/",
    "agents/prompts/planner/",
    "apps/cli/planner/",
    "tests/agents/graph/planner/",
    "tests/integration/agents/planner/",
    "tests/apps/cli/planner/",
]


@mcp.tool()
async def classify_fix(worktree_path: str, base_branch: str) -> str:
    """Analyze a worktree's changes to determine if the fix requires restarting
    from plan creation or just re-running the executor.

    Inspects git diff between the worktree HEAD and the base branch merge-base.
    If any changed files are in planner code paths, returns restart_plan.
    Otherwise returns restart_executor.

    Args:
        worktree_path: Path to the git worktree with the implemented fix.
        base_branch: The branch the worktree was created from (for merge-base).
    """
    _log(f"classify_fix: worktree={worktree_path} base={base_branch}")

    returncode, stdout, stderr = await _run_subprocess(
        ["git", "diff", "--name-only", f"origin/{base_branch}...HEAD"],
        cwd=worktree_path,
        timeout=30,
    )

    if returncode != 0:
        return json.dumps({"error": f"git diff failed: {stderr}"})

    changed_files = [f.strip() for f in stdout.splitlines() if f.strip()]

    planner_files = [
        f for f in changed_files if any(f.startswith(prefix) for prefix in PLANNER_PATH_PREFIXES)
    ]

    if planner_files:
        return json.dumps(
            {
                "restart_scope": "restart_plan",
                "reason": f"Fix touches planner code: {', '.join(planner_files[:5])}",
                "planner_files": planner_files,
                "all_changed_files": changed_files,
            }
        )

    return json.dumps(
        {
            "restart_scope": "restart_executor",
            "reason": "Fix does not touch planner code — executor re-run is sufficient",
            "planner_files": [],
            "all_changed_files": changed_files,
        }
    )


EXECUTOR_PRESERVE_DIRS = {".agent_data", "plans"}


@mcp.tool()
async def reset_executor(test_dir: str) -> str:
    """Reset executor status and clean the test directory while preserving
    the plan and agent data. Runs ai-executor reset-status then deletes
    everything except .agent_data/ and plans/.

    Args:
        test_dir: Path to the test project directory. Must contain 'playground' in path.
    """
    resolved = os.path.realpath(test_dir)
    _log(f"reset_executor: resolved={resolved}")

    if "playground" not in resolved:
        return json.dumps({"error": "Safety: only playground directories allowed"})

    if not os.path.isdir(resolved):
        return json.dumps({"error": f"Directory does not exist: {resolved}"})

    returncode, stdout, stderr = await _run_subprocess(
        ["ai-executor", "reset-status", "--force", "--no-backup"],
        cwd=resolved,
        timeout=60,
    )

    if returncode != 0:
        return json.dumps(
            {
                "error": "ai-executor reset-status failed",
                "exit_code": returncode,
                "stderr": _truncate(stderr),
            }
        )

    deleted = []
    preserved = []
    for item in os.listdir(resolved):
        if item in EXECUTOR_PRESERVE_DIRS:
            preserved.append(item)
            continue
        path = os.path.join(resolved, item)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
        deleted.append(item)

    return json.dumps(
        {
            "success": True,
            "preserved": preserved,
            "deleted": deleted,
        }
    )


def main():
    """Entry point for the automation-mcp CLI command."""
    mcp.run()


if __name__ == "__main__":
    main()
