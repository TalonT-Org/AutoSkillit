from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, NamedTuple

from autoskillit.core import ensure_project_temp, get_logger

logger = get_logger()


class SidecarReadStatus(StrEnum):
    FOUND = "found"
    MISSING = "missing"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class IssueSidecarEntry:
    issue_url: str
    status: Literal["completed", "failed"]
    ts: str
    pr_url: str | None = None
    reason: str | None = None
    terminal_step: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> IssueSidecarEntry:
        issue_url = data["issue_url"]
        if not isinstance(issue_url, str):
            raise TypeError(f"issue_url must be str, got {type(issue_url).__name__!r}")
        status = data["status"]
        if not isinstance(status, str):
            raise TypeError(f"status must be str, got {type(status).__name__!r}")
        ts_raw = data.get("ts")
        pr_url_raw = data.get("pr_url")
        reason_raw = data.get("reason")
        terminal_step_raw = data.get("terminal_step")
        return cls(
            issue_url=issue_url,
            status=status,  # type: ignore[arg-type]
            ts=str(ts_raw) if ts_raw is not None else "",
            pr_url=str(pr_url_raw) if pr_url_raw is not None else None,
            reason=str(reason_raw) if reason_raw is not None else None,
            terminal_step=str(terminal_step_raw) if terminal_step_raw is not None else None,
        )


class SidecarReadResult(NamedTuple):
    entries: list[IssueSidecarEntry]
    source: SidecarReadStatus


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
            entries.append(IssueSidecarEntry.from_dict(data))
        except (json.JSONDecodeError, KeyError) as exc:
            logger.debug("sidecar: skipping corrupt JSONL line", path=str(path), error=str(exc))
            continue
    return entries


def read_sidecar_from_path(path: Path) -> SidecarReadResult:
    """Read and parse a sidecar JSONL at path.

    Returns a SidecarReadResult with source indicating whether the file was
    found, missing, or unreadable. Callers must inspect source to distinguish
    'file missing' from 'file empty'.
    """
    entries: list[IssueSidecarEntry] = []
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return SidecarReadResult(entries=[], source=SidecarReadStatus.MISSING)
    except OSError as exc:
        logger.warning("sidecar: failed to read file", path=str(path), error=str(exc))
        return SidecarReadResult(entries=[], source=SidecarReadStatus.ERROR)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            entries.append(IssueSidecarEntry.from_dict(data))
        except (json.JSONDecodeError, KeyError) as exc:
            logger.debug("sidecar: skipping corrupt JSONL line", path=str(path), error=str(exc))
            continue
    return SidecarReadResult(entries=entries, source=SidecarReadStatus.FOUND)


def compute_remaining_issues(
    dispatch_id: str, original_urls: list[str], project_dir: Path
) -> list[str]:
    seen = {e.issue_url for e in read_sidecar(dispatch_id, project_dir)}
    return [url for url in original_urls if url not in seen]


def merge_sidecar_chain(dispatch_ids: Sequence[str], project_dir: Path) -> list[IssueSidecarEntry]:
    """Read sidecar entries from multiple dispatch IDs and merge by issue_url.

    When resuming a dispatch that has prior attempt dispatch IDs, their sidecar
    files contain entries for issues already processed. Merging them avoids
    re-processing those issues.
    """
    seen: dict[str, IssueSidecarEntry] = {}
    for did in dispatch_ids:
        for entry in read_sidecar(did, project_dir):
            if entry.issue_url not in seen:
                seen[entry.issue_url] = entry
    return list(seen.values())
