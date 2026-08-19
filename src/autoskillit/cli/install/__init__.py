"""Install/upgrade/plugin-artifact package — re-export facade.

Mirrors cli/doctor's facade pattern. Re-exports the union of each submodule's
public surface: ``_install_contract.__all__``, public names from
``_install_info``, ``InstalledPluginsFile`` from ``_installed_plugins``,
``install``/``upgrade`` from ``_marketplace``, and ``_plugin_artifact.__all__``.
"""

from autoskillit.cli.install._install_contract import (
    InstallFailureKind,
    InstallMode,
    InstallOutcome,
    InstallProcessStatus,
    InstallRequest,
    InstallResult,
    MaintenanceInstallArgv,
    process_status_for_result,
    result_from_process_status,
)
from autoskillit.cli.install._install_info import (
    InstallInfo,
    InstallTrack,
    InstallType,
    classify_track,
    comparison_branch,
    detect_install,
    dismissal_window,
    resolve_autoskillit_entrypoint,
    upgrade_command,
)
from autoskillit.cli.install._installed_plugins import InstalledPluginsFile
from autoskillit.cli.install._marketplace import install, upgrade
from autoskillit.cli.install._plugin_artifact import (
    DefaultPluginRetirementCoordinator,
    InstalledPluginArtifactAuthority,
    InstalledPluginArtifactRetirementOwner,
    current_installed_plugin_authority,
    current_installed_plugin_root,
    default_plugin_retirement_coordinator,
    installed_plugin_artifact_lease_path,
    installed_plugin_artifact_manifest_path,
    installed_plugin_semantic_key,
    interactive_plugin_authority,
    publish_installed_plugin_artifact,
)

__all__ = [
    "InstallFailureKind",
    "InstallMode",
    "InstallOutcome",
    "InstallProcessStatus",
    "InstallRequest",
    "InstallResult",
    "MaintenanceInstallArgv",
    "process_status_for_result",
    "result_from_process_status",
    "InstallType",
    "InstallTrack",
    "InstallInfo",
    "resolve_autoskillit_entrypoint",
    "detect_install",
    "classify_track",
    "comparison_branch",
    "dismissal_window",
    "upgrade_command",
    "InstalledPluginsFile",
    "install",
    "upgrade",
    "DefaultPluginRetirementCoordinator",
    "InstalledPluginArtifactAuthority",
    "InstalledPluginArtifactRetirementOwner",
    "current_installed_plugin_authority",
    "current_installed_plugin_root",
    "default_plugin_retirement_coordinator",
    "installed_plugin_artifact_lease_path",
    "installed_plugin_artifact_manifest_path",
    "installed_plugin_semantic_key",
    "interactive_plugin_authority",
    "publish_installed_plugin_artifact",
]
