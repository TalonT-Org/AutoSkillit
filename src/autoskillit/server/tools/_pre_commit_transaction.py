"""Pre-commit transaction used by the workspace commit tool."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from autoskillit.core import CommitFailureClass, resolve_temp_dir
from autoskillit.execution import build_sanitized_env
from autoskillit.server._subprocess import _run_subprocess
from autoskillit.server.tools._pre_commit_failure import (
    parse_combined_process_output,
    pre_commit_failure_class,
)

__all__ = ["_run_pre_commit_transaction"]


async def _run_pre_commit_transaction(
    cwd: str,
    paths: list[str],
    *,
    workspace_temp_dir: str | None,
) -> dict[str, object] | None:
    """Run hooks, re-stage auto-fixes, and retry once when the tree changed."""
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

    if hook_cmd is None:
        return None

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
    if rc == 0:
        return None

    hook_output = parse_combined_process_output(
        stderr,
        stdout,
        f"pre-commit exited with status {rc}",
    )
    failure_class = pre_commit_failure_class(hook_output)
    rc2, readd_stdout, readd_stderr = await _run_subprocess(
        ["git", "-C", cwd, "add", "--"] + paths, cwd=cwd, timeout=30
    )
    if rc2 != 0:
        readd_output = parse_combined_process_output(
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
        retry_output = parse_combined_process_output(
            stderr3,
            stdout3,
            f"pre-commit retry exited with status {rc3}",
        )
        return {
            "success": False,
            "error": f"pre-commit retry failed: {retry_output}",
            "failure_class": pre_commit_failure_class(retry_output).value,
        }
    return None
