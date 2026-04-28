from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from autoskillit.core import ensure_project_temp, get_logger

logger = get_logger()


@dataclass(frozen=True)
class IssueSidecarEntry:
    issue_url: str
    status: Literal["completed", "failed"]
    ts: str
    pr_url: str | None = None
    reason: str | None = None


def sidecar_path(dispatch_id: str, project_dir: Path) -> Path:
    return ensure_project_temp(project_dir) / "dispatches" / f"{dispatch_id}_issues.jsonl"


def append_sidecar_entry(dispatch_id: str, entry: IssueSidecarEntry, project_dir: Path) -> None:
    path = sidecar_path(dispatch_id, project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: v for k, v in asdict(entry).items() if v is not None}
    with path.open("a") as fh:
        fh.write(json.dumps(payload) + "\n")


def read_sidecar(dispatch_id: str, project_dir: Path) -> list[IssueSidecarEntry]:
    path = sidecar_path(dispatch_id, project_dir)
    if not path.exists():
        return []
    entries: list[IssueSidecarEntry] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            entries.append(
                IssueSidecarEntry(
                    issue_url=data["issue_url"],
                    status=data["status"],
                    ts=data.get("ts", ""),
                    pr_url=data.get("pr_url"),
                    reason=data.get("reason"),
                )
            )
        except (json.JSONDecodeError, KeyError) as exc:
            logger.debug("sidecar: skipping corrupt JSONL line", path=str(path), error=str(exc))
            continue
    return entries


def read_sidecar_from_path(path: Path) -> list[IssueSidecarEntry]:
    """Read and parse a sidecar JSONL at an explicit path.

    Returns parsed entries. Skips corrupt lines. Returns [] on OSError.
    """
    entries: list[IssueSidecarEntry] = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            entries.append(
                IssueSidecarEntry(
                    issue_url=data["issue_url"],
                    status=data["status"],
                    ts=data.get("ts", ""),
                    pr_url=data.get("pr_url"),
                    reason=data.get("reason"),
                )
            )
        except (json.JSONDecodeError, KeyError) as exc:
            logger.debug("sidecar: skipping corrupt JSONL line", path=str(path), error=str(exc))
            continue
    return entries


def compute_remaining_issues(
    dispatch_id: str, original_urls: list[str], project_dir: Path
) -> list[str]:
    seen = {e.issue_url for e in read_sidecar(dispatch_id, project_dir)}
    return [url for url in original_urls if url not in seen]
