from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from autoskillit.fleet.sidecar import (
    IssueSidecarEntry,
    append_sidecar_entry,
    compute_remaining_issues,
)


def write_sidecar_entry(
    dispatch_id: str,
    issue_url: str,
    status: str,
    pr_url: str = "",
    reason: str = "",
    project_dir: str = "",
) -> dict[str, str]:
    """Append one completion entry; callable via run_python. Returns {"ok": "true"} on success."""
    if status not in ("completed", "failed"):
        return {"ok": "false", "error": f"invalid status: {status!r}"}
    entry = IssueSidecarEntry(
        issue_url=issue_url,
        status=cast(Literal["completed", "failed"], status),
        ts=datetime.now(tz=UTC).isoformat(),
        pr_url=pr_url or None,
        reason=reason or None,
    )
    root = Path(project_dir) if project_dir else Path.cwd()
    try:
        append_sidecar_entry(dispatch_id, entry, root)
    except OSError as exc:
        return {"ok": "false", "error": str(exc)}
    return {"ok": "true"}


def get_remaining_issues(
    dispatch_id: str,
    original_urls_json: str,
    project_dir: str = "",
) -> dict[str, str]:
    """Return remaining URLs as remaining_urls_json + remaining_count dict."""
    try:
        original_urls: list[str] = json.loads(original_urls_json)
    except (json.JSONDecodeError, TypeError) as exc:
        return {"ok": "false", "error": f"invalid original_urls_json: {exc}"}
    root = Path(project_dir) if project_dir else Path.cwd()
    remaining = compute_remaining_issues(dispatch_id, original_urls, root)
    return {
        "remaining_urls_json": json.dumps(remaining),
        "remaining_count": str(len(remaining)),
    }
