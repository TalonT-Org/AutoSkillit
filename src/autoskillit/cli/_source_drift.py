"""Source-drift boot gate for autoskillit CLI.

Detects when the running binary's installed commit SHA diverges from the HEAD
of the branch it was installed from (integration, stable, or local editable).

Design principles:
- Fail-open: any error causes a silent DEBUG log and early return.
- Fast-fail: never blocks the CLI for more than a few seconds.
- Guard env vars allow callers (subprocesses, CI) to bypass the gate entirely.
- KeyboardInterrupt propagates — only ``Exception`` is swallowed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from autoskillit.core import get_logger

logger = get_logger(__name__)


class InstallType(StrEnum):
    GIT_VCS = "git-vcs"
    LOCAL_EDITABLE = "local-editable"
    LOCAL_PATH = "local-path"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InstallInfo:
    install_type: InstallType
    commit_id: str | None
    requested_revision: str | None
    url: str | None
    editable_source: Path | None


def detect_install() -> InstallInfo:
    """Classify the autoskillit install from ``direct_url.json`` metadata.

    Returns ``InstallInfo(UNKNOWN, ...)`` on any error or when the metadata is
    absent (e.g. installed via sdist from PyPI without a VCS reference).
    """
    _unknown = InstallInfo(InstallType.UNKNOWN, None, None, None, None)
    try:
        import importlib.metadata

        dist = importlib.metadata.Distribution.from_name("autoskillit")
        raw = dist.read_text("direct_url.json")
        if not raw:
            return _unknown

        data = json.loads(raw)

        vcs_info = data.get("vcs_info", {})
        if isinstance(vcs_info, dict) and vcs_info.get("vcs") == "git":
            return InstallInfo(
                install_type=InstallType.GIT_VCS,
                commit_id=vcs_info.get("commit_id") or None,
                requested_revision=vcs_info.get("requested_revision") or None,
                url=data.get("url") or None,
                editable_source=None,
            )

        dir_info = data.get("dir_info", {})
        url = data.get("url", "")
        if isinstance(dir_info, dict) and dir_info.get("editable") is True:
            if isinstance(url, str) and url.startswith("file://"):
                src_path = url[len("file://") :]
                return InstallInfo(
                    install_type=InstallType.LOCAL_EDITABLE,
                    commit_id=None,
                    requested_revision=None,
                    url=url,
                    editable_source=Path(src_path),
                )

        if isinstance(url, str) and url.startswith("file://"):
            return InstallInfo(
                install_type=InstallType.LOCAL_PATH,
                commit_id=None,
                requested_revision=None,
                url=url,
                editable_source=None,
            )

        return _unknown

    except Exception:
        logger.debug("drift check: detect_install failed", exc_info=True)
        return _unknown


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
            The doctor check uses ``network=False`` to guarantee no outbound calls.
    """
    try:
        # Missing revision guard — bare pip install git+... has no @ref
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
            # No source repo or git failed — fall through to API/cache
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
    url = f"https://api.github.com/repos/TalonT-Org/AutoSkillit/git/refs/heads/{rev}"

    if network:
        from autoskillit.cli._stale_check import _fetch_with_cache

        data: Any = _fetch_with_cache(url, home=home)
    else:
        # Cache-only: read existing disk cache regardless of TTL
        from autoskillit.cli._stale_check import _read_fetch_cache

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


def run_source_drift_check(home: Path | None = None) -> None:
    """Run the source-drift gate on interactive CLI invocations.

    Guards (checked BEFORE the fail-open try/except so KeyboardInterrupt
    during a guard still propagates):
    - ``AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK=1`` — explicit bypass
    - ``CLAUDECODE=1`` — headless/MCP session
    - ``CI=1`` — generic CI environment (GitHub Actions, CircleCI, etc.)
    """
    if (
        os.environ.get("AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK")
        or os.environ.get("CLAUDECODE")
        or os.environ.get("CI")
    ):
        return

    try:
        _home = home or Path.home()
        _skip_env: dict[str, str] = {
            **os.environ,
            "AUTOSKILLIT_SKIP_STALE_CHECK": "1",
            "AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK": "1",
        }

        info = detect_install()

        if info.install_type in (InstallType.UNKNOWN, InstallType.LOCAL_PATH):
            logger.debug("drift check skipped: install type is %s", info.install_type)
            return

        ref_sha = resolve_reference_sha(info, _home, network=True)
        if ref_sha is None:
            return

        if info.commit_id == ref_sha:
            return

        # Drift detected — check dismissal
        from autoskillit.cli._stale_check import (
            _is_drift_dismissed,
            _read_dismiss_state,
            _write_dismiss_state,
        )

        state = _read_dismiss_state(_home)
        if _is_drift_dismissed(state, info.commit_id or "", ref_sha):
            return

        installed_short = (info.commit_id or "unknown")[:8]
        ref_short = ref_sha[:8]
        hint = _fix_hint(info)

        print(
            f"\n[autoskillit] Source drift detected:\n"
            f"  installed: {installed_short} ({info.requested_revision or info.install_type})\n"
            f"  reference: {ref_short}\n"
            f"  Fix: {hint}",
            flush=True,
        )

        if sys.stdin.isatty() and sys.stdout.isatty():
            answer = input("Update now? [Y/n] ").strip().lower()
            if answer in ("", "y", "yes"):
                install_cmd = _install_cmd(info)
                if install_cmd:
                    from autoskillit.cli._terminal import terminal_guard

                    with terminal_guard():
                        subprocess.run(install_cmd, check=False, env=_skip_env)
            else:
                state["source_drift"] = {
                    "dismissed_at": datetime.now(UTC).isoformat(),
                    "installed_sha": info.commit_id or "",
                    "reference_sha": ref_sha,
                }
                _write_dismiss_state(_home, state)
        else:
            print(
                "[autoskillit] source drift detected; update with the command shown above.",
                flush=True,
            )

    except Exception:
        logger.debug("drift check skipped: unexpected error", exc_info=True)


def _fix_hint(info: InstallInfo) -> str:
    """Return the human-readable fix command for the given install classification."""
    if info.install_type == InstallType.GIT_VCS:
        rev = info.requested_revision or ""
        if rev == "integration":
            return "task install-dev"
        if rev == "stable":
            return (
                "curl -fsSL"
                " https://raw.githubusercontent.com/TalonT-Org/AutoSkillit/stable/install.sh"
                " | sh"
            )
        return "reinstall from the source you originally used"
    if info.install_type == InstallType.LOCAL_EDITABLE:
        return "task install-worktree"
    return "reinstall from the source you originally used"


def _install_cmd(info: InstallInfo) -> list[str] | None:
    """Return the subprocess command to run for the given install classification."""
    if info.install_type == InstallType.GIT_VCS:
        rev = info.requested_revision or ""
        if rev == "integration":
            return ["task", "install-dev"]
        if rev == "stable":
            return [
                "sh",
                "-c",
                "curl -fsSL"
                " https://raw.githubusercontent.com/TalonT-Org/AutoSkillit/stable/install.sh"
                " | sh",
            ]
    if info.install_type == InstallType.LOCAL_EDITABLE:
        return ["task", "install-worktree"]
    return None
