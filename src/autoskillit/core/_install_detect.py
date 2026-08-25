"""Install-type detection for feature gating — IL-0."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)  # noqa: TID251 — IL-0 module, no autoskillit imports allowed


class DirectUrlInfo(TypedDict):
    install_type: str
    requested_revision: str | None
    commit_id: str | None
    editable: bool
    url: str


def _unknown_direct_url() -> DirectUrlInfo:
    return {
        "install_type": "unknown",
        "requested_revision": None,
        "commit_id": None,
        "editable": False,
        "url": "",
    }


def _is_release_tag(rev: str) -> bool:
    """Return True if rev looks like a version tag (e.g. 'v0.7.75', '0.7.75')."""
    return bool(re.fullmatch(r"v?\d+(\.\d+)*", rev))


def _is_stable_track(rev: str | None) -> bool:
    return not rev or rev in ("main", "stable") or _is_release_tag(rev)


def _parse_direct_url_text(raw: str | None) -> DirectUrlInfo:
    if not raw:
        return _unknown_direct_url()
    data = json.loads(raw)
    raw_url = data.get("url")
    url = raw_url if isinstance(raw_url, str) else ""
    vcs_info = data.get("vcs_info", {})
    if isinstance(vcs_info, dict) and vcs_info.get("vcs") == "git":
        return {
            "install_type": "git-vcs",
            "requested_revision": vcs_info.get("requested_revision") or None,
            "commit_id": vcs_info.get("commit_id") or None,
            "editable": False,
            "url": url,
        }
    dir_info = data.get("dir_info", {})
    if isinstance(dir_info, dict) and dir_info.get("editable") is True:
        return {
            "install_type": "local-editable",
            "requested_revision": None,
            "commit_id": None,
            "editable": True,
            "url": url,
        }
    if url.startswith("file://"):
        return {
            "install_type": "local-path",
            "requested_revision": None,
            "commit_id": None,
            "editable": False,
            "url": url,
        }
    unknown = _unknown_direct_url()
    unknown["url"] = url
    return unknown


def _staged_dist_info_paths(root: Path) -> tuple[Path, ...]:
    pattern = "autoskillit/lib/python*/site-packages/autoskillit-*.dist-info"
    resolved: dict[str, Path] = {}
    for candidate in root.glob(pattern):
        try:
            path = candidate.resolve()
        except OSError:
            continue
        resolved[str(path)] = path
    return tuple(resolved[key] for key in sorted(resolved))


def _staged_direct_url_paths(root: Path) -> tuple[Path, ...]:
    pattern = "autoskillit/lib/python*/site-packages/autoskillit-*.dist-info/direct_url.json"
    resolved: dict[str, Path] = {}
    for candidate in root.glob(pattern):
        try:
            path = candidate.resolve()
        except OSError:
            continue
        resolved[str(path)] = path
    return tuple(resolved[key] for key in sorted(resolved))


def parse_direct_url(root: Path | None = None) -> DirectUrlInfo:
    """Parse direct_url.json and return a canonical install descriptor.

    Keys: install_type (str), requested_revision (str|None),
          commit_id (str|None), editable (bool), url (str).
    """
    try:
        import importlib.metadata

        if root is None:
            dist = importlib.metadata.Distribution.from_name("autoskillit")
            return _parse_direct_url_text(dist.read_text("direct_url.json"))
        paths = _staged_direct_url_paths(root)
        if paths:
            try:
                raw = paths[0].read_text(encoding="utf-8")
            except OSError:
                return _unknown_direct_url()
            return _parse_direct_url_text(raw)
        return _unknown_direct_url()
    except Exception:
        logger.debug("direct_url.json parsing failed", exc_info=True)
        return _unknown_direct_url()


def distribution_version_at(root: Path) -> str | None:
    """Return the installed distribution version found below a uv tool root."""
    for dist_info in _staged_dist_info_paths(root):
        name = dist_info.name
        prefix = "autoskillit-"
        suffix = ".dist-info"
        if name.startswith(prefix) and name.endswith(suffix):
            version = name[len(prefix) : -len(suffix)]
            if version:
                return version
    return None


def is_dev_install() -> bool:
    """Return True if a development install (editable or dev-track VCS); False on any error."""
    try:
        info = parse_direct_url()
        if info["editable"]:
            return True
        if info["install_type"] == "git-vcs" and not _is_stable_track(info["requested_revision"]):
            return True
        return False
    except Exception:
        logger.debug("install type detection failed", exc_info=True)
        return False
