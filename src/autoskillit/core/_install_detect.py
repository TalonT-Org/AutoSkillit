"""Install-type detection for feature gating — IL-0."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)  # noqa: TID251 — IL-0 module, no autoskillit imports allowed


def is_dev_install() -> bool:
    """Return True if installed in editable mode; False on any error."""
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
