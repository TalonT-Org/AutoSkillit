"""IL-0 Claude Code subprocess env builder, conventions, and feature-flag primitives.

Exposes the canonical public surface of `claude_env`, `conventions`, and
`feature_flags` through the `autoskillit.core.claude_env` namespace.
Backward-compat shims at `core/_claude_env.py`, `core/claude_conventions.py`,
and `core/feature_flags.py` preserve old import paths.
"""

from __future__ import annotations

from autoskillit.core.claude_env.claude_env import (
    IDE_ENV_ALWAYS_EXTRAS,
    IDE_ENV_DENYLIST,
    IDE_ENV_PREFIX_DENYLIST,
    build_agent_env,
    build_claude_env,
    build_maintenance_env,
    resolve_dbus_session_bus_address,
)
from autoskillit.core.claude_env.conventions import (
    ClaudeDirectoryConventions,
    LayoutError,
    validate_add_dir,
    validate_worktree_path,
)
from autoskillit.core.claude_env.feature_flags import (
    _collect_disabled_feature_tags,
    is_feature_enabled,
)

__all__ = [
    "ClaudeDirectoryConventions",
    "IDE_ENV_ALWAYS_EXTRAS",
    "IDE_ENV_DENYLIST",
    "IDE_ENV_PREFIX_DENYLIST",
    "LayoutError",
    "_collect_disabled_feature_tags",
    "build_agent_env",
    "build_claude_env",
    "build_maintenance_env",
    "is_feature_enabled",
    "resolve_dbus_session_bus_address",
    "validate_add_dir",
    "validate_worktree_path",
]
