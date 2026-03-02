"""Utility callables for smoke-test pipeline run_python steps."""

from __future__ import annotations

import json
from pathlib import Path


def check_bug_report_non_empty(workspace: str) -> dict[str, str]:
    """Return {"non_empty": "true"} if bug_report.json exists and is non-empty.

    Called by run_python from the check_summary step in smoke-test.yaml.
    The workspace argument is the root directory initialised by the setup step.

    Known limitation: the filename ``bug_report.json`` is hard-coded. This
    function reads the file written by a preceding pipeline step rather than
    receiving the path through the pipeline's native ``capture:`` data-flow
    mechanism. Future work should pass the path explicitly via the ``args:``
    dict when the smoke-test recipe is updated.
    """
    report = Path(workspace) / "bug_report.json"
    if not report.exists():
        return {"non_empty": "false"}
    try:
        data = json.loads(report.read_text())
        return {"non_empty": "true" if data else "false"}
    except (json.JSONDecodeError, OSError):
        return {"non_empty": "false"}
