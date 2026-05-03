"""Source-repo discovery and SHA resolution extracted from _update_checks.py."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.cli.update._update_checks_fetch import _fetch_with_cache, _read_fetch_cache
from autoskillit.core import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.cli._install_info import InstallInfo


def find_source_repo() -> Path | None:
    """Locate the autoskillit source repository root.

    Resolution order:
    1. ``AUTOSKILLIT_SOURCE_REPO`` env var (must exist and contain ``src/autoskillit/``).
    2. Walk upward from ``Path.cwd()``, match ``pyproject.toml``
       ``[project].name == "autoskillit"`` AND ``src/autoskillit/`` present.

    Returns ``None`` if no match found or on any error.
    """
    try:
        env_val = os.environ.get("AUTOSKILLIT_SOURCE_REPO")
        if env_val:
            candidate = Path(env_val)
            if candidate.exists() and (candidate / "src" / "autoskillit").exists():
                return candidate
            logger.debug(
                "AUTOSKILLIT_SOURCE_REPO=%s not usable (missing or no src/autoskillit/), "
                "falling through to CWD walk",
                env_val,
            )

        import tomllib

        current = Path.cwd()
        while True:
            pyproject = current / "pyproject.toml"
            if pyproject.is_file():
                try:
                    with open(pyproject, "rb") as fh:
                        data = tomllib.load(fh)
                    project_name = data.get("project", {}).get("name")
                    if (
                        project_name == "autoskillit"
                        and (current / "src" / "autoskillit").exists()
                    ):
                        return current
                except Exception:
                    logger.debug("drift check: could not parse %s", pyproject, exc_info=True)

            parent = current.parent
            if parent == current:  # Filesystem root
                break
            current = parent

        return None
    except Exception:
        logger.debug("drift check: find_source_repo failed", exc_info=True)
        return None


def resolve_reference_sha(
    info: InstallInfo,
    home: Path,
    *,
    network: bool = True,
) -> str | None:
    """Resolve the current HEAD SHA of the branch the install was tracking.

    Returns ``None`` when the SHA cannot be determined (network offline, no
    source repo, unknown revision).  The caller treats ``None`` as "skip check"
    (fail-open).

    Args:
        info: Install classification from ``detect_install()``.
        home: User home directory (used by the disk-backed fetch cache).
        network: When ``False``, only the local git or disk cache is consulted.
            The doctor check passes ``network=True`` so it can resolve remote refs.
    """
    try:
        if info.requested_revision is None:
            logger.debug("drift check skipped: no requested_revision in direct_url.json")
            return None

        rev = info.requested_revision

        # Short-circuit: exact SHA equality means no drift is possible.
        # IMPORTANT: use == not startswith — a branch named after a hex prefix
        # of the commit SHA must NOT false-positive here.
        if rev == info.commit_id:
            return info.commit_id

        sha: str | None = None

        source_repo = find_source_repo()
        if source_repo is not None and source_repo.exists():
            sha = _git_ls_remote_sha(source_repo, rev)

        if sha is None:
            sha = _api_sha(rev, home, network=network)

        return sha

    except Exception:
        logger.debug("drift check skipped: resolve_reference_sha error", exc_info=True)
        return None


def _git_ls_remote_sha(source_repo: Path, rev: str) -> str | None:
    """Run git ls-remote to resolve a branch or tag ref SHA.

    Tries ``refs/heads/<rev>`` first, then ``refs/tags/<rev>^{}`` for peeled
    tag objects.  Returns ``None`` on empty output or any subprocess error.
    """
    for ref in (f"refs/heads/{rev}", f"refs/tags/{rev}^{{}}"):
        try:
            result = subprocess.run(
                ["git", "-C", str(source_repo), "ls-remote", "origin", ref],
                capture_output=True,
                text=True,
                timeout=5,
                env=os.environ,
            )
            first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
            if first_line:
                sha = first_line.split()[0]
                if sha:
                    return sha
        except (subprocess.SubprocessError, FileNotFoundError, OSError, IndexError):
            logger.debug("git ls-remote failed for ref=%s", ref, exc_info=True)
    return None


def _api_sha(rev: str, home: Path, *, network: bool = True) -> str | None:
    """Return the commit SHA for ``rev`` from the GitHub API or disk cache.

    When ``network=False`` (doctor mode), reads the existing cache without
    any TTL check and makes no outbound HTTP request.  Returns ``None`` if the
    cache has no entry for the URL.
    """
    # Try refs/heads first; fall back to refs/tags for tag revisions.
    ref_prefix = "refs/tags" if rev.startswith("v") else "refs/heads"
    url = f"https://api.github.com/repos/TalonT-Org/AutoSkillit/git/{ref_prefix}/{rev}"

    if network:
        data: Any = _fetch_with_cache(url, home=home)
    else:
        cache = _read_fetch_cache(home)
        entry = cache.get(url) if isinstance(cache.get(url), dict) else None
        data = entry.get("body") if isinstance(entry, dict) else None

    if not isinstance(data, dict):
        return None
    obj = data.get("object")
    if not isinstance(obj, dict):
        return None
    sha = obj.get("sha")
    return sha if isinstance(sha, str) and sha else None
