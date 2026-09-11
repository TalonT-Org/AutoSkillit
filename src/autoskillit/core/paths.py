"""Backward-compat shim for paths — see core.io.paths."""

import os  # noqa: F401  # re-exported for monkeypatch.setattr("autoskillit.core.paths.os", ...)
import subprocess  # noqa: F401  # re-exported for monkeypatch.setattr("autoskillit.core.paths.subprocess", ...)
import sys  # noqa: F401  # re-exported for monkeypatch.setattr("autoskillit.core.paths.sys", ...)

from autoskillit.core.io.paths import (
    GENERATED_FILES,
    _find_git_ancestor,
    _GitAncestorKind,
    claude_code_log_path,
    claude_code_project_dir,
    default_log_dir,
    destination_location,
    find_latest_session_id,
    is_generated_path,
    is_git_main_checkout,
    is_git_worktree,
    is_in_git_repo,
    pkg_root,
    resolve_main_worktree,
    resolve_project_dir,
)

__all__ = [
    "GENERATED_FILES",
    "_GitAncestorKind",
    "_find_git_ancestor",
    "claude_code_log_path",
    "claude_code_project_dir",
    "default_log_dir",
    "destination_location",
    "find_latest_session_id",
    "is_generated_path",
    "is_git_main_checkout",
    "is_git_worktree",
    "is_in_git_repo",
    "pkg_root",
    "resolve_main_worktree",
    "resolve_project_dir",
]
