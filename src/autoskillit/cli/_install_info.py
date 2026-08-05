"""Install classification and update policy for autoskillit CLI.

Mostly pure — no network, no subprocess. ``detect_install()`` does read-only
local I/O: ``direct_url.json`` package metadata (via ``parse_direct_url()``)
and a PATH lookup for the running CLI's own entrypoint (via
``shutil.which()``, resolved pre-pivot for ``InstallInfo.entrypoint`` — see
its docstring). Every other function in this module is pure, deriving
everything from an already-constructed ``InstallInfo``.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

from autoskillit.core import _is_stable_track, get_logger, parse_direct_url

logger = get_logger(__name__)

_INSTALL_FROM_DEVELOP = "git+https://github.com/TalonT-Org/AutoSkillit.git@develop"
_STABLE_DISMISS_WINDOW = timedelta(days=7)
_DEV_DISMISS_WINDOW = timedelta(hours=12)


class InstallType(StrEnum):
    GIT_VCS = "git-vcs"
    LOCAL_EDITABLE = "local-editable"
    LOCAL_PATH = "local-path"
    UNKNOWN = "unknown"


class InstallTrack(StrEnum):
    STABLE = "stable"
    DEV = "dev"
    LOCAL = "local"


@dataclass(frozen=True, slots=True)
class InstallInfo:
    install_type: InstallType
    commit_id: str | None
    requested_revision: str | None
    url: str | None
    editable_source: Path | None
    entrypoint: Path | None = None
    """The running autoskillit CLI's own executable, resolved pre-pivot.

    Populated by ``detect_install()`` via ``shutil.which("autoskillit")``
    against the ambient (pre-maintenance, richest-PATH) environment — safe
    because it runs before any update subprocess. For a ``uv tool``
    install this resolves to the uv tool shim (typically
    ``~/.local/bin/autoskillit``), which ``uv`` rewrites in place during an
    upgrade rather than recreating, so the path survives a venv rebuild.
    This is exactly the executable a post-pivot version probe
    (``cli/update/_transaction.py``'s ``fresh_version_prober``) needs to
    invoke, even when the maintenance environment's own ``PATH`` (copied
    verbatim from a possibly PATH-poor background caller — see
    ``core/_claude_env.py``) would fail to locate it. ``None`` when no such
    executable is discoverable (e.g. running from an uninstalled checkout).
    """


def _resolve_entrypoint() -> Path | None:
    """Resolve the running autoskillit CLI's entrypoint against the ambient PATH."""
    resolved = shutil.which("autoskillit")
    return Path(resolved) if resolved is not None else None


def detect_install() -> InstallInfo:
    """Classify the autoskillit install from ``direct_url.json`` metadata.

    Returns ``InstallInfo(UNKNOWN, ...)`` on any error or when the metadata is
    absent (e.g. installed via sdist from PyPI without a VCS reference).
    """
    _unknown = InstallInfo(InstallType.UNKNOWN, None, None, None, None)
    try:
        info = parse_direct_url()
        entrypoint = _resolve_entrypoint()
        url = info["url"] or ""
        if info["install_type"] == "git-vcs":
            return InstallInfo(
                install_type=InstallType.GIT_VCS,
                commit_id=info["commit_id"],
                requested_revision=info["requested_revision"],
                url=url or None,
                editable_source=None,
                entrypoint=entrypoint,
            )
        if info["install_type"] == "local-editable":
            if isinstance(url, str) and url.startswith("file://"):
                src_path = url[len("file://") :]
                return InstallInfo(
                    install_type=InstallType.LOCAL_EDITABLE,
                    commit_id=None,
                    requested_revision=None,
                    url=url,
                    editable_source=Path(src_path),
                    entrypoint=entrypoint,
                )
        if info["install_type"] == "local-path":
            return InstallInfo(
                install_type=InstallType.LOCAL_PATH,
                commit_id=None,
                requested_revision=None,
                url=url or None,
                editable_source=None,
                entrypoint=entrypoint,
            )
        return _unknown
    except Exception:
        logger.debug("install classification failed", exc_info=True)
        return _unknown


def classify_track(info: InstallInfo) -> InstallTrack:
    if info.install_type in (InstallType.LOCAL_EDITABLE, InstallType.LOCAL_PATH):
        return InstallTrack.LOCAL
    rev = info.requested_revision or ""
    if _is_stable_track(rev):
        return InstallTrack.STABLE
    return InstallTrack.DEV


def comparison_branch(info: InstallInfo) -> str | None:
    """Return the GitHub branch/tag to compare for update availability.

    - stable / main / release-tag / UNKNOWN → ``"releases/latest"``
    - any other GIT_VCS revision (dev-track) → ``"develop"``
    - ``LOCAL_EDITABLE`` / ``LOCAL_PATH`` → ``None`` (not applicable)
    """
    track = classify_track(info)
    if track == InstallTrack.LOCAL:
        return None
    if track == InstallTrack.DEV:
        return "develop"
    return "releases/latest"


def dismissal_window(info: InstallInfo) -> timedelta:
    """Return the dismissal cooldown for this install type.

    Branch-aware windows:

    - stable / main / release-tag / UNKNOWN → ``timedelta(days=7)``
    - dev-track / LOCAL → ``timedelta(hours=12)``
    """
    track = classify_track(info)
    # LOCAL_EDITABLE is reachable only via AUTOSKILLIT_FORCE_UPDATE_CHECK; not dead code.
    if track in (InstallTrack.DEV, InstallTrack.LOCAL):
        return _DEV_DISMISS_WINDOW
    return _STABLE_DISMISS_WINDOW


def upgrade_command(info: InstallInfo) -> list[str] | None:
    """Return the subprocess command to upgrade autoskillit for this install.

    - stable / main / release-tag →
      ``["uv", "tool", "upgrade", "autoskillit", "--python", "<major>.<minor>"]``
    - dev-track →
      ``["uv", "tool", "install", "--force", <git URL>, "--python", "<major>.<minor>"]``
    - ``LOCAL_EDITABLE`` → ``["uv", "pip", "install", "-e", str(info.editable_source)]``
    - ``UNKNOWN`` / ``LOCAL_PATH`` → ``None``

    Both ``uv tool`` shapes pin ``--python`` to the CURRENTLY RUNNING
    interpreter's major.minor, captured here at command-construction time
    (pre-pivot, while the parent's own interpreter is still authoritative).
    A routine update then rebuilds the venv on the same interpreter line, so
    the venv's ``lib/pythonX.Y`` layout — and every artifact deriving from
    it — is stable across updates; an interpreter migration becomes an
    explicit operator action instead of a side effect of update timing
    (issue #4469: the incident's venv silently flipped from 3.13 to 3.11
    mid-update). If the pinned minor is unavailable, ``uv`` fails loudly
    pre-pivot rather than silently choosing a different one — strictly
    better than an unpinned command that could resolve to any interpreter.
    ``LOCAL_EDITABLE`` installs into an existing venv via ``uv pip install``
    rather than rebuilding one, so no pin applies there.
    """
    if info.install_type == InstallType.LOCAL_EDITABLE and info.editable_source is not None:
        return ["uv", "pip", "install", "-e", str(info.editable_source)]
    if info.install_type != InstallType.GIT_VCS:
        return None
    python_pin = f"{sys.version_info.major}.{sys.version_info.minor}"
    track = classify_track(info)
    if track == InstallTrack.DEV:
        return ["uv", "tool", "install", "--force", _INSTALL_FROM_DEVELOP, "--python", python_pin]
    return ["uv", "tool", "upgrade", "autoskillit", "--python", python_pin]
