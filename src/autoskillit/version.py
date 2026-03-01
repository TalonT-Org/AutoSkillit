"""Version health utilities (Layer 0)."""

from __future__ import annotations

import functools
import importlib.resources as ir
import json
from pathlib import Path

from autoskillit import __version__


@functools.lru_cache(maxsize=1)
def version_info(plugin_dir: Path | str | None = None) -> dict:
    """Return version health for the autoskillit installation.

    Args:
        plugin_dir: Root of the plugin directory (must contain .claude-plugin/).
            When None, defaults to the autoskillit package directory.
    """
    if plugin_dir is None:
        plugin_dir = Path(str(ir.files("autoskillit")))
    plugin_json_path = Path(plugin_dir) / ".claude-plugin" / "plugin.json"
    plugin_version = None
    if plugin_json_path.is_file():
        data = json.loads(plugin_json_path.read_text())
        plugin_version = data.get("version")
    return {
        "package_version": __version__,
        "plugin_json_version": plugin_version,
        "match": __version__ == plugin_version,
    }
