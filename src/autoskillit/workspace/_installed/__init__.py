"""IL-1 install/lease/artifact authority facade.

Re-exports the public surface of the install-state, installed-artifact, and
publication-obligation modules through the ``autoskillit.workspace._installed``
namespace. ``_projection_cache.py`` and ``_shared_asset_store.py`` are consumed only
via their own submodule paths (never through this facade), matching how they were
reached before this package existed.
"""

from __future__ import annotations

from ._artifact import (
    InstalledArtifactVerification,
    InstallStateFinding,
    InstallStateLeaseMode,
    InstallStateSpec,
    verify_installed_plugin_artifact,
    write_installed_plugin_artifact_manifest_locked,
)
from ._state import (
    marketplace_plugin_root,
    reconcile_install_artifacts,
    verify_install_state,
)
from ._update_obligation import (
    PublicationObligation,
    clear_obligation,
    read_obligation,
    update_obligation_expected_version,
    write_obligation,
)

__all__ = [
    "InstallStateFinding",
    "InstallStateLeaseMode",
    "InstallStateSpec",
    "InstalledArtifactVerification",
    "PublicationObligation",
    "clear_obligation",
    "marketplace_plugin_root",
    "read_obligation",
    "reconcile_install_artifacts",
    "update_obligation_expected_version",
    "verify_install_state",
    "verify_installed_plugin_artifact",
    "write_installed_plugin_artifact_manifest_locked",
    "write_obligation",
]
