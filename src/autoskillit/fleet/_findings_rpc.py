from __future__ import annotations

import json
import os
from pathlib import Path

from autoskillit.core import get_logger
from autoskillit.fleet.sidecar import compute_remaining_issues

logger = get_logger(__name__)


def parse_and_resume(
    issue_urls_csv: str,
    project_dir: str = "",
    dispatch_id: str = "",
) -> dict[str, str]:
    """Parse CSV issue URLs and filter out already-completed issues via sidecar.

    Callable via run_python. Falls back to AUTOSKILLIT_DISPATCH_ID from env if
    dispatch_id is not provided.
    Returns {"remaining_urls_json": "[...]", "completed_count": "N"} on success
    or {"ok": "false", "error": "..."} on failure.
    """
    if not dispatch_id:
        dispatch_id = os.environ.get("AUTOSKILLIT_DISPATCH_ID", "")
    if not dispatch_id:
        return {"ok": "false", "error": "AUTOSKILLIT_DISPATCH_ID env var not set"}
    urls = [u.strip() for u in issue_urls_csv.split(",") if u.strip()]
    root = Path(project_dir) if project_dir else Path.cwd()
    try:
        remaining = compute_remaining_issues(dispatch_id, urls, root)
    except Exception as exc:
        logger.warning("parse_and_resume: compute_remaining_issues failed", exc_info=True)
        return {"ok": "false", "error": str(exc)}
    completed = len(urls) - len(remaining)
    return {
        "remaining_urls_json": json.dumps(remaining),
        "completed_count": str(completed),
    }


def load_execution_map(map_path: str) -> dict[str, str]:
    """Read a BEM JSON file and extract the groups array.

    Callable via run_python.
    Returns {"groups_json": "[{...}]", "total_groups": "N"} on success
    or {"error": "..."} on failure.
    """
    path = Path(map_path)
    if not path.exists():
        return {"error": f"BEM file not found: {map_path}"}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return {"error": f"failed to read BEM file: {exc}"}
    if not isinstance(data, dict) or "groups" not in data:
        return {"error": "BEM file missing top-level 'groups' key"}
    groups = data["groups"]
    if not isinstance(groups, list):
        return {"error": f"BEM 'groups' must be a list, got {type(groups).__name__}"}
    return {
        "groups_json": json.dumps(groups),
        "total_groups": str(len(groups)),
    }
