"""Backward-compat shim for claude_conventions — see core.claude_env.conventions."""

from autoskillit.core.claude_env.conventions import (
    ClaudeDirectoryConventions,
    LayoutError,
    validate_add_dir,
    validate_worktree_path,
)

__all__ = [
    "ClaudeDirectoryConventions",
    "LayoutError",
    "validate_add_dir",
    "validate_worktree_path",
]
