"""Canonical GitHub remote repository resolver.

L1 module: resolves 'owner/repo' from a git working directory,
encoding the clone isolation contract (upstream > origin priority).
"""

from __future__ import annotations

import asyncio

from autoskillit.core import get_logger, normalize_owner_repo, parse_github_repo

_log = get_logger(__name__)


async def resolve_remote_repo(
    cwd: str,
    hint: str | None = None,
    remotes: tuple[str, ...] = ("upstream", "origin"),
) -> str | None:
    """Resolve GitHub 'owner/repo' from a git working directory.

    Priority:
      1. hint — if already owner/repo format, return as-is (strips .git); if a full URL, parse it.
      2. Each remote in `remotes` (default: upstream first, then origin).
         The default order encodes the clone isolation contract: upstream holds
         the real GitHub URL; origin may be a file:// isolation URL.

    Returns owner/repo string or None if no GitHub remote is found.
    """
    if hint:
        normalized = normalize_owner_repo(hint)
        if normalized:
            return normalized
        parsed = parse_github_repo(hint)
        if parsed:
            return parsed

    for remote in remotes:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "remote",
                "get-url",
                remote,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                parsed = parse_github_repo(stdout.decode().strip())
                if parsed:
                    return parsed
        except OSError:
            _log.warning("Failed to get URL for remote %r in %r", remote, cwd, exc_info=True)

    return None
