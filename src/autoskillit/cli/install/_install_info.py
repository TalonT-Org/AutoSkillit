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
    file_url_path,
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
    local_source: Path | None = None
    """The source directory a LOCAL_PATH install was built from."""


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
            editable_source = file_url_path(url)
            if editable_source is not None:
                return InstallInfo(
                    install_type=InstallType.LOCAL_EDITABLE,
                    commit_id=None,
                    requested_revision=None,
                    url=url,
                    editable_source=editable_source,
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
                local_source=file_url_path(url),
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

    Environment overrides are non-empty only for the GIT_VCS dev track and
    LOCAL_PATH: they install into a caller-chosen destination via
    ``UV_TOOL_DIR`` rather than force-replacing the single shared uv tool root.
    ``uv tool install`` (uv 0.9.21) has no ``--target``/per-install destination
    flag — ``UV_TOOL_DIR`` is the sole supported redirection mechanism,
    confirmed by spike against a real git-sourced install.
    """

    argv: Sequence[str]
    mutates_shared_root: bool
    env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))


def _install_from_commit(commit: str) -> str:
    return f"{_INSTALL_REPOSITORY}@{commit}"


def _staged_tool_env(install_root_destination: Path | None) -> tuple[dict[str, str], bool]:
    """Return the ``UV_TOOL_DIR`` staging env and whether the shared root is mutated."""
    if install_root_destination is None:
        return {}, True
    bin_dir = install_root_destination.parent / f".{install_root_destination.name}-bin"
    return {
        "UV_TOOL_DIR": str(install_root_destination),
        "UV_TOOL_BIN_DIR": str(bin_dir),
    }, False


def upgrade_command(
    info: InstallInfo,
    *,
    install_root_destination: Path | None = None,
    pin_commit: str | None = None,
) -> UpgradeCommand | None:
    """Build the track-aware upgrade command, pinned to this Python minor.

    ``install_root_destination``, when given, redirects the GIT_VCS dev-track
    and LOCAL_PATH installs into that directory via ``UV_TOOL_DIR`` instead of
    force-replacing the shared uv-managed tool root. ``UV_TOOL_BIN_DIR`` is
    redirected alongside it into a sibling throwaway directory so uv's own
    generated console-script symlink never lands at ``~/.local/bin/autoskillit``
    and clobbers the AutoSkillit-owned entrypoint shim published there. Ignored
    by the STABLE and LOCAL_EDITABLE tracks, which upgrade in place.
    """
    python_pin = f"{sys.version_info.major}.{sys.version_info.minor}"
    match info.install_type:
        case InstallType.LOCAL_EDITABLE:
            if info.editable_source is None:
                return None
            return UpgradeCommand(
                argv=["uv", "pip", "install", "-e", str(info.editable_source)],
                mutates_shared_root=True,
            )
        case InstallType.LOCAL_PATH:
            if info.local_source is None:
                return None
            env, mutates_shared_root = _staged_tool_env(install_root_destination)
            return UpgradeCommand(
                argv=[
                    "uv",
                    "tool",
                    "install",
                    "--force",
                    "--reinstall",
                    str(info.local_source),
                    "--python",
                    python_pin,
                ],
                mutates_shared_root=mutates_shared_root,
                env=env,
            )
        case InstallType.GIT_VCS:
            if classify_track(info) != InstallTrack.DEV:
                return UpgradeCommand(
                    argv=["uv", "tool", "upgrade", "autoskillit", "--python", python_pin],
                    mutates_shared_root=True,
                )
            requirement = _install_from_commit(pin_commit) if pin_commit else _INSTALL_FROM_DEVELOP
            env, mutates_shared_root = _staged_tool_env(install_root_destination)
            return UpgradeCommand(
                argv=["uv", "tool", "install", "--force", requirement, "--python", python_pin],
                mutates_shared_root=mutates_shared_root,
                env=env,
            )
        case InstallType.UNKNOWN:
            return None
        case unhandled:
            assert_never(unhandled)


def upgrade_unavailable_message(info: InstallInfo) -> str:
    """Name the install type and its remedy when ``upgrade_command`` returns ``None``."""
    install_type = info.install_type
    match install_type:
        case InstallType.UNKNOWN | InstallType.GIT_VCS:
            # GIT_VCS is unreachable under production: ``upgrade_command`` for
            # GIT_VCS always returns a non-None command. It is grouped with
            # UNKNOWN so that tests which monkeypatch ``upgrade_command`` to
            # return ``None`` still get a graceful message instead of a crash.
            return (
                f"Install type '{install_type.value}' has no upgrade command. Reinstall via "
                "install.sh (stable) or 'task install-dev' (develop)."
            )
        case InstallType.LOCAL_PATH:
            return (
                f"Install type '{install_type.value}' has no recorded source directory. "
                "Reinstall with 'uv tool install --force --reinstall <autoskillit checkout>' "
                "or 'task install-dev' (develop)."
            )
        case InstallType.LOCAL_EDITABLE:
            return (
                f"Install type '{install_type.value}' has no recorded source directory. "
                "Reinstall with 'uv pip install -e <autoskillit checkout>' "
                "or 'task install-dev' (develop)."
            )
        case unhandled:
            assert_never(unhandled)


def normalized_package_version() -> str | None:
    """Return the running package's ``__version__`` string, or ``None`` if invalid.

    Centralizes the missing/invalid-version guard so the doctor and update-check
    callers share one implementation. The isinstance/strip guards exist for
    tests that ``delattr(__version__)``.
    """
    from packaging.version import InvalidVersion, Version

    import autoskillit as _pkg

    current = getattr(_pkg, "__version__", None)
    if not isinstance(current, str) or not current.strip():
        return None
    try:
        Version(current)
    except InvalidVersion:
        return None
    return current
