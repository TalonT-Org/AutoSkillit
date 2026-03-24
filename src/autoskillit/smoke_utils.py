"""Utility callables for smoke-test pipeline run_python steps.

Known limitation: functions use hardcoded path conventions from the pipeline recipe.
"""

from __future__ import annotations

import json
from pathlib import Path


def check_bug_report_non_empty(workspace: str) -> dict[str, str]:
    """Return {"non_empty": "true"} if bug_report.json exists and is non-empty.

    Called by run_python from the check_summary step in smoke-test.yaml.
    The workspace argument is the root directory initialised by the setup step.
    """
    report = Path(workspace) / "bug_report.json"
    if not report.exists():
        return {"non_empty": "false"}
    try:
        data = json.loads(report.read_text())
        return {"non_empty": "true" if data else "false"}
    except (json.JSONDecodeError, OSError):
        return {"non_empty": "false"}


def check_cleanup_mode(defer_cleanup: str) -> dict[str, str]:
    """Route helper: return deferred='true' when defer_cleanup is truthy, else 'false'.

    Used by check_defer_cleanup and check_defer_on_failure recipe steps to choose
    between immediate per-pipeline cleanup and deferred batch cleanup.
    """
    return {"deferred": "true" if defer_cleanup.lower() == "true" else "false"}
