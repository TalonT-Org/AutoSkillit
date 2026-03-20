"""Canonical GitHub remote URL parser.

L0 module: stdlib only, zero autoskillit imports.
"""

from __future__ import annotations

import re

_GITHUB_REPO_RE = re.compile(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$")
_OWNER_REPO_RE = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
_FULL_URL_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)")
_SHORTHAND_RE = re.compile(r"^([^/]+)/([^#]+)#(\d+)$")


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


def _parse_issue_ref(issue_ref: str) -> tuple[str, str, int]:
    """Parse owner, repo, number from a GitHub issue reference.

    Accepts:
    - Full URL: https://github.com/owner/repo/issues/42
    - Shorthand: owner/repo#42

    Raises ValueError for unrecognised formats (including bare numbers).
    Bare number resolution is the caller's responsibility.
    """
    m = _FULL_URL_RE.match(issue_ref.strip())
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    m = _SHORTHAND_RE.match(issue_ref.strip())
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    raise ValueError(
        f"Cannot parse GitHub issue reference: {issue_ref!r}. "
        "Expected a full URL (https://github.com/owner/repo/issues/N) "
        "or shorthand (owner/repo#N)."
    )
