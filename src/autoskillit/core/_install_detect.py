"""Install-type detection for feature gating — IL-0 (zero autoskillit imports).

is_dev_install() is the canonical predicate for determining whether the
current autoskillit install is a development (editable) install. Used by
config resolution to auto-detect the experimental_enabled default.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)  # noqa: TID251 — IL-0 module, no autoskillit imports allowed


def is_dev_install() -> bool:
    """Return True if autoskillit is installed in editable (development) mode.

    Reads direct_url.json from package metadata — the same mechanism used
    by _version_snapshot._install_info() and cli._install_info.detect_install().

    Returns False on any error (missing metadata, malformed JSON, etc.).
    """
    try:
        import importlib.metadata

        dist = importlib.metadata.Distribution.from_name("autoskillit")
        raw = dist.read_text("direct_url.json")
        if not raw:
            return False
        data = json.loads(raw)
        dir_info = data.get("dir_info", {})
        if isinstance(dir_info, dict) and dir_info.get("editable") is True:
            return True
        return False
    except Exception:
        logger.debug("install type detection failed", exc_info=True)
        return False
