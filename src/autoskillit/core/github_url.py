"""Backward-compat shim for github_url — see core.git.github_url."""

from autoskillit.core.git.github_url import (
    _parse_issue_ref,
    normalize_owner_repo,
    parse_github_repo,
)

__all__ = ["_parse_issue_ref", "normalize_owner_repo", "parse_github_repo"]
