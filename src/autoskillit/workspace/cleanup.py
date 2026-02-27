"""Worktree and directory teardown utilities.

No dependency on MCP, config, types, or other autoskillit modules beyond _logging.py.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from autoskillit.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CleanupResult:
    deleted: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.failed) == 0

    def to_dict(self) -> dict:  # type: ignore[type-arg]
        return {
            "success": self.success,
            "deleted": self.deleted,
            "failed": [{"path": p, "error": e} for p, e in self.failed],
            "skipped": self.skipped,
        }


def _delete_directory_contents(
    directory: Path,
    preserve: set[str] | None = None,
) -> CleanupResult:
    """Delete all items in directory, skipping preserved names.

    Never raises. All errors captured in CleanupResult.failed.
    FileNotFoundError treated as success (item already gone).
    """
    result = CleanupResult()
    for item_name in os.listdir(directory):
        if preserve and item_name in preserve:
            result.skipped.append(item_name)
            continue
        path = directory / item_name
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            result.deleted.append(item_name)
        except FileNotFoundError:
            result.deleted.append(item_name)  # gone = success
        except OSError as exc:
            result.failed.append((item_name, f"{type(exc).__name__}: {exc}"))
    return result
