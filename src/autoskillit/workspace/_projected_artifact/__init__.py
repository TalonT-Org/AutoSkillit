"""Projected plugin artifact lifecycle facade."""

from .authority import (
    ProjectedPluginArtifactAuthority,
    project_default_plugin_authority,
    project_direct_install_authority,
)

__all__ = [
    "ProjectedPluginArtifactAuthority",
    "project_default_plugin_authority",
    "project_direct_install_authority",
]
