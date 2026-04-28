from __future__ import annotations

import json
import os
from pathlib import Path

from autoskillit.fleet.sidecar import compute_remaining_issues


def parse_and_resume(
    issue_urls_csv: str,
    project_dir: str = "",
) -> dict[str, str]:
    """Parse CSV issue URLs and filter out already-completed issues via sidecar.

    Callable via run_python. Reads AUTOSKILLIT_DISPATCH_ID from env.
    Returns {"remaining_urls_json": "[...]", "completed_count": "N"} on success
    or {"error": "..."} on failure.
    """
    dispatch_id = os.environ.get("AUTOSKILLIT_DISPATCH_ID", "")
    if not dispatch_id:
        return {"error": "AUTOSKILLIT_DISPATCH_ID env var not set"}
    urls = [u.strip() for u in issue_urls_csv.split(",") if u.strip()]
    root = Path(project_dir) if project_dir else Path.cwd()
    try:
        remaining = compute_remaining_issues(dispatch_id, urls, root)
    except OSError as exc:
        return {"error": str(exc)}
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
    return {
        "groups_json": json.dumps(groups),
        "total_groups": str(len(groups)),
    }
