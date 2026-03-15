"""Canonical GitHub remote URL parser.

L0 module: stdlib only, zero autoskillit imports.
"""

from __future__ import annotations

import re

_GITHUB_REPO_RE = re.compile(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$")
_OWNER_REPO_RE = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")


def normalize_owner_repo(hint: str) -> str | None:
    """Return normalized owner/repo if hint is already in that format, else None.

    Strips a trailing .git suffix if present.
    Accepts alphanumeric, hyphens, underscores, and dots in each segment.
    """
    candidate = hint[:-4] if hint.endswith(".git") else hint
    if _OWNER_REPO_RE.match(candidate):
        return candidate
    return None


def parse_github_repo(url: str) -> str | None:
    """Return 'owner/repo' from a GitHub remote URL, or None for any non-GitHub URL.

    Supports HTTPS and SSH formats:
      https://github.com/owner/repo.git  →  owner/repo
      git@github.com:owner/repo.git      →  owner/repo
      https://github.com/owner/repo      →  owner/repo

    Returns None for:
      file:// URLs (clone isolation — no github.com domain)
      Non-GitHub hosts (gitlab.com, etc.)
      URLs with extra path segments (github.com/owner/repo/extra)
      Empty strings
    """
    if not url:
        return None
    m = _GITHUB_REPO_RE.search(url)
    return m.group(1) if m else None
