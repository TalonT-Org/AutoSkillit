"""IL-0 git remote, GitHub URL parsing, test-gate detection, and branch-guard primitives.

Exposes the canonical public surface of ``git_remote``, ``github_url``,
``bash_write_targets``, and ``branch_guard`` through the
``autoskillit.core.git`` namespace. Backward-compat shims at
``core/git_remote.py``, ``core/github_url.py``,
and ``core/branch_guard.py`` preserve old
import paths after the core/git/ decomposition.
"""

from __future__ import annotations

from autoskillit.core.git.bash_write_targets import (
    contains_test_gate_command,
)
from autoskillit.core.git.branch_guard import (
    is_protected_branch,
)
from autoskillit.core.git.git_refs import (
    ResolvedRef,
    local_branch_ref,
    remote_tracking_ref,
    verify_qualified_ref_sync,
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
    "ResolvedRef",
    "_parse_issue_ref",
    "contains_test_gate_command",
    "is_protected_branch",
    "local_branch_ref",
    "normalize_owner_repo",
    "parse_github_repo",
    "parse_github_remote_url",
    "resolve_clone_remote_name_sync",
    "resolve_repository_remote_identity_sync",
    "remote_tracking_ref",
    "verify_qualified_ref_sync",
]
