"""Subprocess execution helpers for MCP tools."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import TerminationReason

if TYPE_CHECKING:
    from autoskillit.core import SubprocessResult


def _get_ctx():  # type: ignore[return]
    """Deferred import of _get_ctx from _state to avoid circular imports."""
    from autoskillit.server._state import _get_ctx as _ctx_fn

    return _ctx_fn()


def _process_runner_result(
    result: SubprocessResult,
    timeout: float,
) -> tuple[int, str, str]:
    """Convert a SubprocessResult to (returncode, stdout, stderr).

    Translates TIMED_OUT termination into (-1, stdout, "Process timed out after {timeout}s").
    """
    if result.termination == TerminationReason.TIMED_OUT:
        return -1, result.stdout, f"Process timed out after {timeout}s"
    return result.returncode, result.stdout, result.stderr


_GH_API_SUBCOMMANDS: frozenset[str] = frozenset(
    {"api", "pr", "issue", "repo", "release", "run", "workflow", "search"}
)


def _is_github_cli_call(cmd: list[str]) -> bool:
    return len(cmd) >= 2 and cmd[0] == "gh" and cmd[1] in _GH_API_SUBCOMMANDS


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

    is_gh = _is_github_cli_call(cmd)
    start = time.monotonic() if is_gh else 0.0

    result = await runner(cmd, cwd=Path(cwd), timeout=timeout, env=env)
    returncode, stdout, stderr = _process_runner_result(result, timeout)

    if is_gh:
        latency_ms = (time.monotonic() - start) * 1000.0
        log = _get_ctx().github_api_log
        if log is not None:
            await log.record_gh_cli(
                subcommand=" ".join(str(c) for c in cmd[:3]),
                exit_code=returncode,
                latency_ms=latency_ms,
                timestamp=datetime.now(UTC).isoformat(),
            )

    return returncode, stdout, stderr
