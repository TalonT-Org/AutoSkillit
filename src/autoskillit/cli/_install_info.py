"""Install classification and update policy for autoskillit CLI.

Pure module — no I/O, no network, no subprocess.  Provides the canonical
source of truth for install-type classification and the three policy helpers
that drive the unified update check.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

from autoskillit.core import get_logger

logger = get_logger(__name__)

_INSTALL_FROM_DEVELOP = "git+https://github.com/TalonT-Org/AutoSkillit.git@develop"


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
        logger.debug("install classification failed", exc_info=True)
        return _unknown


def _is_release_tag(rev: str) -> bool:
    """Return True if ``rev`` looks like a version tag (e.g. 'v0.7.75', '0.7.75')."""
    return bool(re.fullmatch(r"v?\d+(\.\d+)*", rev))


def comparison_branch(info: InstallInfo) -> str | None:
    """Return the GitHub branch/tag to compare for update availability.

    - ``stable`` / ``main`` / release-tag / ``UNKNOWN`` → ``"releases/latest"``
    - ``develop`` → ``"develop"``
    - ``LOCAL_EDITABLE`` / ``LOCAL_PATH`` → ``None`` (not applicable)
    """
    if info.install_type in (InstallType.LOCAL_EDITABLE, InstallType.LOCAL_PATH):
        return None
    rev = info.requested_revision or ""
    if rev == "develop":
        return "develop"
    return "releases/latest"


def dismissal_window(info: InstallInfo) -> timedelta:
    """Return the dismissal cooldown for this install type.

    Branch-aware windows:

    - ``stable`` / ``main`` / release-tag / ``UNKNOWN`` → ``timedelta(days=7)``
    - ``develop`` / ``LOCAL_EDITABLE`` → ``timedelta(hours=12)``
    """
    rev = info.requested_revision or ""
    # LOCAL_EDITABLE is reachable only via AUTOSKILLIT_FORCE_UPDATE_CHECK; not dead code.
    if rev == "develop" or info.install_type == InstallType.LOCAL_EDITABLE:
        return timedelta(hours=12)
    return timedelta(days=7)


def upgrade_command(info: InstallInfo) -> list[str] | None:
    """Return the subprocess command to upgrade autoskillit for this install.

    - ``stable`` / ``main`` / release-tag → ``["uv", "tool", "upgrade", "autoskillit"]``
    - ``develop`` → ``["uv", "tool", "install", "--force", <git URL>]``
    - ``LOCAL_EDITABLE`` → ``["uv", "pip", "install", "-e", str(info.editable_source)]``
    - ``UNKNOWN`` / ``LOCAL_PATH`` → ``None``
    """
    if info.install_type == InstallType.GIT_VCS:
        rev = info.requested_revision or ""
        if rev == "develop":
            return ["uv", "tool", "install", "--force", _INSTALL_FROM_DEVELOP]
        return ["uv", "tool", "upgrade", "autoskillit"]
    if info.install_type == InstallType.LOCAL_EDITABLE and info.editable_source is not None:
        return ["uv", "pip", "install", "-e", str(info.editable_source)]
    return None
