"""Worktree and directory teardown utilities."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from autoskillit.core import CleanupResult, get_logger

logger = get_logger(__name__)


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


class DefaultWorkspaceManager:
    """Concrete WorkspaceManager backed by _delete_directory_contents."""

    def delete_contents(
        self,
        directory: Path,
        preserve: set[str] | None = None,
    ) -> CleanupResult:
        return _delete_directory_contents(directory, preserve)
