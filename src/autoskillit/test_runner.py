"""Pytest output parsing and test pass/fail adjudication.

L3 service module. Used by server.py test_check tool and git_operations.py
merge test gate. No autoskillit server imports — depends only on stdlib and
_logging.
"""

from __future__ import annotations

import re

from autoskillit._logging import get_logger

logger = get_logger(__name__)

_OUTCOME_PATTERN = re.compile(
    r"(\d+)\s+(passed|failed|error|xfailed|xpassed|skipped|warnings?|deselected)"
)


def parse_pytest_summary(stdout: str) -> dict[str, int]:
    """Extract pytest outcome counts from the last ``=``-delimited summary line.

    Pytest's summary line is always delimited by ``=`` characters, e.g.
    ``= 5 passed, 1 warning in 2.31s =``. Only lines that start and end
    with ``=`` are considered, preventing false matches on log output
    containing phrases like ``"3 failed connections"``.

    Returns empty dict if no summary line found.
    """
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not (stripped.startswith("=") and stripped.endswith("=")):
            continue
        matches = _OUTCOME_PATTERN.findall(stripped)
        if matches:
            counts: dict[str, int] = {}
            for count_str, outcome in matches:
                key = outcome.rstrip("s") if outcome == "warnings" else outcome
                counts[key] = int(count_str)
            return counts
    return {}


def check_test_passed(returncode: int, stdout: str) -> bool:
    """Determine test pass/fail with cross-validation.

    Uses exit code as primary signal, but overrides to False if the
    output contains failure indicators — defense against exit code bugs
    in external tools (e.g. Taskfile PIPESTATUS in non-bash shell).
    """
    if returncode != 0:
        return False
    counts = parse_pytest_summary(stdout)
    if counts.get("failed", 0) > 0 or counts.get("error", 0) > 0:
        return False
    return True
