"""Canonical accessor for ~/.claude/plugins/installed_plugins.json.

The real file structure produced by `claude plugin install` is:
    {"version": 2, "plugins": {"autoskillit@autoskillit-local": {...}}}

All access to this file must go through InstalledPluginsFile so that the
nesting contract is defined in exactly one place.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from autoskillit.core import atomic_write

_log = logging.getLogger(__name__)  # noqa: TID251


def _default_path() -> Path:
    return Path.home() / ".claude" / "plugins" / "installed_plugins.json"


class InstalledPluginsFile:
    """Repository over installed_plugins.json.

    Exposes typed query and mutation methods; enforces the {'plugins': {}} nesting.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_path()

    @property
    def path(self) -> Path:
        return self._path

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except json.JSONDecodeError as exc:
            _log.warning("installed_plugins.json is corrupt (%s): %s", self._path, exc)
            return {}
        except OSError as exc:
            _log.warning("Could not read installed_plugins.json (%s): %s", self._path, exc)
            return {}

    def get_plugins(self) -> dict[str, Any]:
        """Return the nested plugins dict (never raises)."""
        return self._read().get("plugins", {})

    def contains(self, plugin_ref: str) -> bool:
        """Return True iff plugin_ref is present in data['plugins']."""
        return plugin_ref in self.get_plugins()

    def remove(self, plugin_ref: str) -> None:
        """Remove plugin_ref from data['plugins'] and atomically rewrite the file.

        No-op if the file does not exist or the key is absent.
        """
        if not self._path.exists():
            return
        data = self._read()
        plugins = data.get("plugins", {})
        if plugin_ref not in plugins:
            return
        del plugins[plugin_ref]
        data["plugins"] = plugins
        atomic_write(self._path, json.dumps(data, indent=2))
