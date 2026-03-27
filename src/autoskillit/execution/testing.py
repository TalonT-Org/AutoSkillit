"""Pytest output parsing and test pass/fail adjudication.

L3 service module. Used by server.py test_check tool and git_operations.py
merge test gate. No autoskillit server imports — depends only on stdlib and
_logging.
"""

from __future__ import annotations

import os
import re
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
        command = self._config.test_check.command
        timeout = float(self._config.test_check.timeout)
        env = build_sanitized_env()
        result = await self._runner(command, cwd=cwd, timeout=timeout, env=env)
        passed = check_test_passed(result.returncode, result.stdout, result.stderr)
        return TestResult(passed=passed, stdout=result.stdout, stderr=result.stderr)
