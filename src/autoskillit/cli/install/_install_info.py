"""Install classification and update policy for autoskillit CLI.

Mostly pure — no network, no subprocess. ``detect_install()`` does read-only
local I/O: ``direct_url.json`` package metadata (via ``parse_direct_url()``)
and pre-pivot resolution of the running CLI's own entrypoint. Every other
function in this module is pure, deriving everything from an
already-constructed ``InstallInfo``.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import assert_never

from autoskillit.core import (
    ReleaseChannel,
    ReleaseIdentity,
    _is_stable_track,
    distribution_version_at,
    get_logger,
    parse_direct_url,
)

logger = get_logger(__name__)

_INSTALL_REPOSITORY = "git+https://github.com/TalonT-Org/AutoSkillit.git"
_INSTALL_FROM_DEVELOP = f"{_INSTALL_REPOSITORY}@develop"
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
    """The executable running this CLI, resolved before an update pivot."""


def resolve_autoskillit_entrypoint(
    *invocation_candidates: str | Path | None,
    search_path: str | None = None,
) -> Path | None:
    """Resolve an executable invocation, then fall back to ``search_path``."""
    candidates = invocation_candidates or (sys.argv[0],)
    for raw_candidate in candidates:
        if raw_candidate is None:
            continue
        candidate = Path(raw_candidate)
        if candidate.name not in {"autoskillit", "autoskillit.exe"}:
            continue
        candidate = candidate if candidate.is_absolute() else candidate.absolute()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    resolved = shutil.which("autoskillit", path=search_path)
    if resolved is None:
        return None
    candidate = Path(resolved)
    return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None


def detect_install() -> InstallInfo:
    """Classify the autoskillit install from ``direct_url.json`` metadata.

    Returns ``InstallInfo(UNKNOWN, ...)`` on any error or when the metadata is
    absent (e.g. installed via sdist from PyPI without a VCS reference).
    """
    _unknown = InstallInfo(InstallType.UNKNOWN, None, None, None, None)
    try:
        info = parse_direct_url()
        entrypoint = resolve_autoskillit_entrypoint()
        url = info["url"] or ""
        if info["install_type"] == "git-vcs":
            if info["commit_id"] is None or info["requested_revision"] is None:
                return _unknown
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


def release_identity(info: InstallInfo, *, version: str) -> ReleaseIdentity:
    """Construct the running install's identity from explicit version metadata."""
    track = classify_track(info)
    match track:
        case InstallTrack.STABLE:
            return ReleaseIdentity(ReleaseChannel.RELEASED, version=version)
        case InstallTrack.DEV:
            return ReleaseIdentity(
                ReleaseChannel.BRANCH,
                version=version,
                commit=info.commit_id,
                ref=info.requested_revision,
            )
        case InstallTrack.LOCAL:
            return ReleaseIdentity(ReleaseChannel.WORKING_TREE, version=version)
        case unhandled:
            assert_never(unhandled)


def installed_identity_at(
    root: Path,
    *,
    channel: ReleaseChannel,
) -> ReleaseIdentity | None:
    """Read a release identity from an installed uv tool root."""
    version = distribution_version_at(root)
    if version is None:
        return None
    match channel:
        case ReleaseChannel.RELEASED:
            return ReleaseIdentity(channel, version=version)
        case ReleaseChannel.BRANCH:
            direct_url = parse_direct_url(root)
            commit = direct_url["commit_id"]
            ref = direct_url["requested_revision"]
            if commit is None or ref is None:
                return None
            return ReleaseIdentity(channel, version=version, commit=commit, ref=ref)
        case ReleaseChannel.WORKING_TREE:
            return ReleaseIdentity(channel, version=version)
        case unhandled:
            assert_never(unhandled)


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


@dataclass(frozen=True, slots=True)
class UpgradeCommand:
    """Track-aware upgrade argv, plus any environment overrides it requires.

    Environment overrides are non-empty only for the GIT_VCS dev track: it
    installs into a caller-chosen destination via ``UV_TOOL_DIR`` rather than
    force-replacing the single shared uv tool root. ``uv tool install`` (uv
    0.9.21) has no ``--target``/per-install destination flag — ``UV_TOOL_DIR``
    is the sole supported redirection mechanism, confirmed by spike against a
    real git-sourced install.
    """

    argv: Sequence[str]
    mutates_shared_root: bool
    env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))


def _install_from_commit(commit: str) -> str:
    return f"{_INSTALL_REPOSITORY}@{commit}"


def upgrade_command(
    info: InstallInfo,
    *,
    install_root_destination: Path | None = None,
    pin_commit: str | None = None,
) -> UpgradeCommand | None:
    """Build the track-aware upgrade command, pinned to this Python minor.

    ``install_root_destination``, when given, redirects the GIT_VCS dev-track
    install into that directory via ``UV_TOOL_DIR`` instead of force-replacing
    the shared uv-managed tool root. ``UV_TOOL_BIN_DIR`` is redirected
    alongside it into a sibling throwaway directory so uv's own generated
    console-script symlink never lands at ``~/.local/bin/autoskillit`` and
    clobbers the AutoSkillit-owned entrypoint shim published there. Ignored by
    every other branch — the STABLE and LOCAL_EDITABLE tracks are unchanged.
    """
    if info.install_type == InstallType.LOCAL_EDITABLE and info.editable_source is not None:
        return UpgradeCommand(
            argv=["uv", "pip", "install", "-e", str(info.editable_source)],
            mutates_shared_root=True,
        )
    if info.install_type != InstallType.GIT_VCS:
        return None
    python_pin = f"{sys.version_info.major}.{sys.version_info.minor}"
    track = classify_track(info)
    if track != InstallTrack.DEV:
        return UpgradeCommand(
            argv=["uv", "tool", "upgrade", "autoskillit", "--python", python_pin],
            mutates_shared_root=True,
        )
    requirement = _install_from_commit(pin_commit) if pin_commit else _INSTALL_FROM_DEVELOP
    argv = ["uv", "tool", "install", "--force", requirement, "--python", python_pin]
    if install_root_destination is None:
        return UpgradeCommand(argv=argv, mutates_shared_root=True)
    bin_dir = install_root_destination.parent / f".{install_root_destination.name}-bin"
    return UpgradeCommand(
        argv=argv,
        mutates_shared_root=False,
        env=MappingProxyType(
            {
                "UV_TOOL_DIR": str(install_root_destination),
                "UV_TOOL_BIN_DIR": str(bin_dir),
            }
        ),
    )
