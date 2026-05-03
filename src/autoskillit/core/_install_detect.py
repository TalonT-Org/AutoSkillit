"""Install-type detection for feature gating — IL-0."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)  # noqa: TID251 — IL-0 module, no autoskillit imports allowed


def _is_release_tag(rev: str) -> bool:
    """Return True if rev looks like a version tag (e.g. 'v0.7.75', '0.7.75')."""
    return bool(re.fullmatch(r"v?\d+(\.\d+)*", rev))


def _is_stable_track(rev: str | None) -> bool:
    return not rev or rev in ("main", "stable") or _is_release_tag(rev)


def parse_direct_url() -> dict[str, Any]:
    """Parse direct_url.json and return a canonical install descriptor.

    Keys: install_type (str), requested_revision (str|None),
          commit_id (str|None), editable (bool), url (str).
    """
    try:
        import importlib.metadata

        dist = importlib.metadata.Distribution.from_name("autoskillit")
        raw = dist.read_text("direct_url.json")
        if not raw:
            return {
                "install_type": "unknown",
                "requested_revision": None,
                "commit_id": None,
                "editable": False,
                "url": "",
            }
        data = json.loads(raw)
        url = data.get("url", "") or ""
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
        if isinstance(url, str) and url.startswith("file://"):
            return {
                "install_type": "local-path",
                "requested_revision": None,
                "commit_id": None,
                "editable": False,
                "url": url,
            }
        return {
            "install_type": "unknown",
            "requested_revision": None,
            "commit_id": None,
            "editable": False,
            "url": url,
        }
    except Exception:
        logger.debug("direct_url.json parsing failed", exc_info=True)
        return {
            "install_type": "unknown",
            "requested_revision": None,
            "commit_id": None,
            "editable": False,
            "url": "",
        }


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
