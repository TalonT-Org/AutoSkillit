"""Backward-compat shim for git_remote — see core.git.git_remote."""

from autoskillit.core.git.git_remote import (
    REMOTE_PRECEDENCE,
    GitHubRepositoryRef,
    RemoteIdentityProbe,
    RemoteIdentityResolution,
    parse_github_remote_url,
    resolve_clone_remote_name_sync,
    resolve_repository_remote_identity_sync,
)

__all__ = [
    "REMOTE_PRECEDENCE",
    "GitHubRepositoryRef",
    "RemoteIdentityProbe",
    "RemoteIdentityResolution",
    "parse_github_remote_url",
    "resolve_clone_remote_name_sync",
    "resolve_repository_remote_identity_sync",
]
