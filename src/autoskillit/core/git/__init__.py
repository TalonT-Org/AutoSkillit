"""IL-0 git remote, GitHub URL parsing, bash write-target extraction, and branch-guard primitives.

Exposes the canonical public surface of ``git_remote``, ``github_url``,
``bash_write_targets``, and ``branch_guard`` through the
``autoskillit.core.git`` namespace. Backward-compat shims at
``core/git_remote.py``, ``core/github_url.py``,
``core/bash_write_targets.py``, and ``core/branch_guard.py`` preserve old
import paths (issue #4671 Phase B decomposition).
"""

from __future__ import annotations

from autoskillit.core.git.bash_write_targets import (
    contains_test_gate_command,
    extract_bash_write_targets,
)
from autoskillit.core.git.branch_guard import (
    is_protected_branch,
)
from autoskillit.core.git.git_remote import (
    REMOTE_PRECEDENCE,
    GitHubRepositoryRef,
    RemoteIdentityProbe,
    RemoteIdentityResolution,
    parse_github_remote_url,
    resolve_clone_remote_name_sync,
    resolve_repository_remote_identity_sync,
)
from autoskillit.core.git.github_url import (
    _parse_issue_ref,
    normalize_owner_repo,
    parse_github_repo,
)

__all__ = [
    "GitHubRepositoryRef",
    "REMOTE_PRECEDENCE",
    "RemoteIdentityProbe",
    "RemoteIdentityResolution",
    "_parse_issue_ref",
    "contains_test_gate_command",
    "extract_bash_write_targets",
    "is_protected_branch",
    "normalize_owner_repo",
    "parse_github_repo",
    "parse_github_remote_url",
    "resolve_clone_remote_name_sync",
    "resolve_repository_remote_identity_sync",
]
