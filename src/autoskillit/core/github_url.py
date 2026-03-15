"""Canonical GitHub remote URL parser.

L0 module: stdlib only, zero autoskillit imports.
"""

from __future__ import annotations

import re

_GITHUB_REPO_RE = re.compile(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$")


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
