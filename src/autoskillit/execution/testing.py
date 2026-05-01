"""Pytest output parsing and test pass/fail adjudication.

IL-1 module. Used by server.py (IL-3) test_check tool and git_operations.py
merge test gate. No autoskillit server imports — depends only on stdlib and
IL-0 _logging.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS, TestResult, get_logger

if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig
    from autoskillit.core import SubprocessRunner

logger = get_logger(__name__)


def build_sanitized_env() -> dict[str, str]:
    """Return a copy of os.environ with server-private env vars removed.

    Server-private vars (AUTOSKILLIT_PRIVATE_ENV_VARS) control MCP server
    behavior and must not be inherited by user-code subprocesses (e.g., pytest
    runs launched by test_check). Callers passing this dict as env= to a
    subprocess runner get full env inheritance minus the internal vars.
    """
    return {k: v for k, v in os.environ.items() if k not in AUTOSKILLIT_PRIVATE_ENV_VARS}


def _read_sidecar_base_branch(cwd: Path) -> str | None:
    """Read base-branch from worktree sidecar if cwd is a linked worktree.

    Worktree sidecars are written by implement-worktree skills at
    ``<project_root>/.autoskillit/temp/worktrees/<wt_name>/base-branch``.
    Returns None if cwd is not a worktree or no sidecar exists.
    """
    git_path = cwd / ".git"
    if not git_path.is_file():
        return None
    try:
        content = git_path.read_text().strip()
        if not content.startswith("gitdir:"):
            return None
        gitdir = Path(content.split(":", 1)[1].strip())
        if not gitdir.is_absolute():
            gitdir = (cwd / gitdir).resolve()
        main_git = gitdir.parent.parent
        project_root = main_git.parent
        wt_name = cwd.name
        sidecar = project_root / ".autoskillit" / "temp" / "worktrees" / wt_name / "base-branch"
        if sidecar.is_file():
            return sidecar.read_text().strip() or None
    except (OSError, ValueError):
        pass
    return None


async def _resolve_base_ref(
    config_base_ref: str | None,
    cwd: Path,
    *,
    default_base_branch: str | None = None,
) -> str | None:
    """Resolve the base ref for test filtering.

    Resolution chain (first non-None wins):
    1. Config override (explicit base_ref in TestCheckConfig)
    2. Worktree sidecar (base-branch file written by implement-worktree skills)
    3. Git upstream tracking ref (``@{upstream}`` of current branch)
    4. default_base_branch (from BranchingConfig)
    5. None (no base ref available)
    """
    if config_base_ref is not None:
        return config_base_ref

    sidecar_ref = _read_sidecar_base_branch(cwd)
    if sidecar_ref:
        return sidecar_ref

    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--abbrev-ref",
            "@{upstream}",
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=15.0)
        except TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except OSError:
                pass
            return None
        if proc.returncode == 0:
            assert proc.stdout is not None
            raw = await proc.stdout.read()
            ref = raw.decode().strip()
            if ref:
                return ref
    except OSError:
        pass

    if default_base_branch is not None:
        return default_base_branch

    return None


_OUTCOME_PATTERN = re.compile(
    r"(\d+)\s+(passed|failed|errors?|xfailed|xpassed|skipped|warnings?|deselected)"
)
_BARE_TIME_ANCHOR = re.compile(r"\bin\s+\d+(?:\.\d+)?s\b")


def _matches_to_counts(matches: list[tuple[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for count_str, outcome in matches:
        key = outcome.rstrip("s") if outcome in ("warnings", "errors") else outcome
        counts[key] = int(count_str)
    return counts


def parse_pytest_summary(stdout: str) -> dict[str, int]:
    """Extract pytest outcome counts from stdout using two-pass detection.

    Pass 1 (=-delimited): Scans in reverse for the last line that both starts
    and ends with ``=``, e.g. ``= 5 passed, 1 warning in 2.31s =``.

    Pass 2 (bare -q): If Pass 1 finds nothing, scans in reverse for a line
    anchored by a pytest timing suffix ``in N.Ns``, e.g. ``3 failed, 97 passed
    in 2.31s``.  The time anchor prevents false matches on log lines that
    mention counts but have no timing suffix.

    Returns empty dict if neither pass finds a summary line.
    """
    lines = stdout.splitlines()

    # Pass 1: =-delimited summary line (pytest default verbosity)
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith("=") and stripped.endswith("="):
            matches = _OUTCOME_PATTERN.findall(stripped)
            if matches:
                return _matches_to_counts(matches)

    # Pass 2: bare -q format anchored by timing suffix
    for line in reversed(lines):
        stripped = line.strip()
        if _BARE_TIME_ANCHOR.search(stripped):
            matches = _OUTCOME_PATTERN.findall(stripped)
            if matches:
                return _matches_to_counts(matches)

    return {}


def check_test_passed(returncode: int, stdout: str, stderr: str = "") -> bool:
    """Determine test pass/fail with cross-validation.

    Uses exit code as primary signal and cross-validates against parsed output
    when pytest-format output is detected. For non-pytest runners that produce
    no pytest summary, trusts the exit code directly.

    Known limitation: if pytest crashes with exit code 0 before printing its
    summary line (e.g. a conftest module that calls sys.exit(0) during
    collection), no summary is found and this function returns True. There is
    no reliable heuristic to distinguish that case from a legitimate non-pytest
    runner that exits 0 with no summary output.
    """
    if returncode != 0:
        return False
    combined = stdout + ("\n" + stderr if stderr else "")
    counts = parse_pytest_summary(combined)
    if not counts:
        # No pytest summary detected — runner is not pytest; trust exit code.
        return True
    if counts.get("failed", 0) > 0 or counts.get("error", 0) > 0:
        return False
    return True


class DefaultTestRunner:
    """Concrete TestRunner that runs the configured test command via subprocess."""

    def __init__(self, config: AutomationConfig, runner: SubprocessRunner) -> None:
        self._config = config
        self._runner = runner

    async def run(self, cwd: Path) -> TestResult:
        effective_commands = self._config.test_check.effective_commands
        timeout = float(self._config.test_check.timeout)
        env = build_sanitized_env()

        filter_mode = self._config.test_check.filter_mode
        if filter_mode:
            env["AUTOSKILLIT_TEST_FILTER"] = filter_mode

        base_ref = await _resolve_base_ref(
            self._config.test_check.base_ref,
            cwd,
            default_base_branch=self._config.branching.default_base_branch,
        )
        if base_ref:
            env["AUTOSKILLIT_TEST_BASE_REF"] = base_ref

        fd, sidecar_path = tempfile.mkstemp(suffix=".json", prefix="filter-stats-")
        os.close(fd)
        env["AUTOSKILLIT_FILTER_STATS_FILE"] = sidecar_path

        total = len(effective_commands)
        stdout_parts: list[str] = []
        last_result = None
        elapsed: float = 0.0
        stat_filter_mode: str | None = None
        stat_tests_selected: int | None = None
        stat_tests_deselected: int | None = None
        start = time.monotonic()
        deadline = start + timeout

        try:
            for idx, command in enumerate(effective_commands, 1):
                remaining = deadline - time.monotonic()
                if remaining < 0.01:
                    break
                result = await self._runner(command, cwd=cwd, timeout=remaining, env=env)
                last_result = result
                if total > 1:
                    stdout_parts.append(
                        f"=== [{idx}/{total}] {' '.join(command)} ===\n{result.stdout}"
                    )
                else:
                    stdout_parts.append(result.stdout)
                if result.returncode != 0:
                    break

            elapsed = time.monotonic() - start

            sidecar = Path(sidecar_path)
            if sidecar.is_file() and sidecar.stat().st_size > 0:
                try:
                    raw = json.loads(sidecar.read_text())
                    if isinstance(raw, dict):
                        fm = raw.get("filter_mode")
                        ts = raw.get("tests_selected")
                        td = raw.get("tests_deselected")
                        stat_filter_mode = fm if isinstance(fm, str) else None
                        stat_tests_selected = ts if isinstance(ts, int) else None
                        stat_tests_deselected = td if isinstance(td, int) else None
                except (json.JSONDecodeError, OSError) as exc:
                    logger.debug("filter stats sidecar read error: %s", exc)
        finally:
            Path(sidecar_path).unlink(missing_ok=True)

        combined_stdout = "\n".join(stdout_parts)
        if last_result is not None:
            final_returncode = last_result.returncode
            final_stderr = last_result.stderr
        else:
            final_returncode = 1
            final_stderr = "timeout exhausted before first command could run"
            logger.warning("test_runner_timeout_before_first_command", timeout=timeout)

        passed = check_test_passed(final_returncode, combined_stdout, final_stderr)
        return TestResult(
            passed=passed,
            stdout=combined_stdout,
            stderr=final_stderr,
            duration_seconds=elapsed,
            filter_mode=stat_filter_mode,
            tests_selected=stat_tests_selected,
            tests_deselected=stat_tests_deselected,
        )
