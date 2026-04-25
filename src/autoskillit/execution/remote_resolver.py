"""Canonical GitHub remote repository resolver.

L1 module: resolves 'owner/repo' from a git working directory,
encoding the clone isolation contract (upstream > origin priority).
"""

from __future__ import annotations

import asyncio

from autoskillit.core import get_logger, normalize_owner_repo, parse_github_repo

_log = get_logger(__name__)

# Canonical remote precedence order: upstream before origin encodes the clone
# isolation contract (upstream = real GitHub URL; origin = file:// local path).
REMOTE_PRECEDENCE: tuple[str, ...] = ("upstream", "origin")


async def resolve_remote_repo(
    cwd: str,
    hint: str | None = None,
    remotes: tuple[str, ...] = REMOTE_PRECEDENCE,
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
            io_task = asyncio.ensure_future(proc.communicate())
            try:
                await asyncio.wait_for(proc.wait(), timeout=15.0)
            except TimeoutError:
                io_task.cancel()
                proc.kill()
                await proc.wait()
                await asyncio.gather(io_task, return_exceptions=True)
                _log.warning("Timed out getting URL for remote %r in %r", remote, cwd)
                continue
            stdout, _ = await io_task
            if proc.returncode == 0:
                parsed = parse_github_repo(stdout.decode().strip())
                if parsed:
                    return parsed
        except OSError:
            _log.warning("Failed to get URL for remote %r in %r", remote, cwd, exc_info=True)

    return None


async def resolve_remote_name(
    cwd: str,
    remotes: tuple[str, ...] = REMOTE_PRECEDENCE,
) -> str:
    """Return the git remote name to use for fetch/rebase operations.

    Tries remotes in precedence order (upstream before origin).
    Rejects file:// URLs — those indicate a clone-isolation origin
    that should not be used for real git operations.
    Falls back to "origin" if no remote qualifies.
    """
    for name in remotes:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "remote",
                "get-url",
                name,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
            if proc.returncode != 0:
                continue
            url = stdout.decode().strip()
            if url.startswith("file://"):
                continue
            return name
        except (TimeoutError, OSError):
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except OSError:
                    pass
            continue
    return "origin"
