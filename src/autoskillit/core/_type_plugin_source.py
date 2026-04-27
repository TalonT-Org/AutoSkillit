"""PluginSource discriminated union — how autoskillit is loaded into Claude Code.

Replaces the `plugin_dir: str | None` nullable sentinel in ToolContext.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["DirectInstall", "MarketplaceInstall", "PluginSource"]


@dataclass(frozen=True)
class DirectInstall:
    """Plugin loaded via --plugin-dir. plugin_dir is the package root (pkg_root())."""

    plugin_dir: Path


@dataclass(frozen=True)
class MarketplaceInstall:
    """Plugin loaded via Claude marketplace.

    cache_path is the installPath field from installed_plugins.json.
    """

    cache_path: Path


PluginSource = DirectInstall | MarketplaceInstall
