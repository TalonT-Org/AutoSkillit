"""Git-index inventory helpers shared by test-side repository guards."""

from __future__ import annotations

import subprocess
from pathlib import Path

_GIT_TIMEOUT_SECONDS = 10


def git_ls_files(
    repo_root: Path,
    *pathspecs: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    """Return NUL-delimited paths tracked by Git under ``repo_root``.

    A missing match is an error by default so exhaustive test inventories do not
    silently become vacuous. Callers that intentionally assert an empty result
    must opt in with ``allow_empty=True``.
    """
    pathspec_description = ", ".join(repr(pathspec) for pathspec in pathspecs) or "<all>"
    command = ["git", "-C", str(repo_root), "ls-files", "-z", "--", *pathspecs]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = str(exc)
        stderr = getattr(exc, "stderr", None)
        if isinstance(stderr, str) and stderr.strip():
            detail = f"{detail}: {stderr.strip()}"
        raise RuntimeError(
            "git ls-files failed for pathspec(s) "
            f"{pathspec_description} in repository {repo_root}: {detail}"
        ) from exc

    paths = tuple(path for path in result.stdout.split("\0") if path)
    if not paths and not allow_empty:
        raise RuntimeError(
            "git ls-files returned no tracked paths for pathspec(s) "
            f"{pathspec_description} in repository {repo_root}"
        )
    return paths
