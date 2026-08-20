"""Install/upgrade/plugin-artifact package — lazy re-export facade.

Mirrors cli/doctor's facade pattern. The submodule import graph is heavy
(``_marketplace`` transitively pulls in ``autoskillit.workspace``, ``_hooks``,
``_init_helpers``, ``_install_snapshot``) and most consumers only need the
leaf ``_install_info`` symbols, which themselves are imported function-locally
at ~10 call sites. To preserve that isolation, the package exposes its
re-export surface through a PEP 562 ``__getattr__`` instead of importing the
submodules eagerly. Tests that already reach the submodules via
``from autoskillit.cli.install import _marketplace`` continue to work because
Python populates submodule attributes on first access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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

# Map public name -> (submodule, attr) for lazy resolution. Keeping the table
# here (instead of an actual eager re-import) means a `from autoskillit.cli.install
# import X` does NOT execute the heavy install-cluster import graph until X is
# actually used.
_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "InstallFailureKind": ("autoskillit.cli.install._install_contract", "InstallFailureKind"),
    "InstallMode": ("autoskillit.cli.install._install_contract", "InstallMode"),
    "InstallOutcome": ("autoskillit.cli.install._install_contract", "InstallOutcome"),
    "InstallProcessStatus": ("autoskillit.cli.install._install_contract", "InstallProcessStatus"),
    "InstallRequest": ("autoskillit.cli.install._install_contract", "InstallRequest"),
    "InstallResult": ("autoskillit.cli.install._install_contract", "InstallResult"),
    "MaintenanceInstallArgv": (
        "autoskillit.cli.install._install_contract",
        "MaintenanceInstallArgv",
    ),
    "process_status_for_result": (
        "autoskillit.cli.install._install_contract",
        "process_status_for_result",
    ),
    "result_from_process_status": (
        "autoskillit.cli.install._install_contract",
        "result_from_process_status",
    ),
    "InstallType": ("autoskillit.cli.install._install_info", "InstallType"),
    "InstallTrack": ("autoskillit.cli.install._install_info", "InstallTrack"),
    "InstallInfo": ("autoskillit.cli.install._install_info", "InstallInfo"),
    "resolve_autoskillit_entrypoint": (
        "autoskillit.cli.install._install_info",
        "resolve_autoskillit_entrypoint",
    ),
    "detect_install": ("autoskillit.cli.install._install_info", "detect_install"),
    "classify_track": ("autoskillit.cli.install._install_info", "classify_track"),
    "comparison_branch": ("autoskillit.cli.install._install_info", "comparison_branch"),
    "dismissal_window": ("autoskillit.cli.install._install_info", "dismissal_window"),
    "upgrade_command": ("autoskillit.cli.install._install_info", "upgrade_command"),
    "InstalledPluginsFile": (
        "autoskillit.cli.install._installed_plugins",
        "InstalledPluginsFile",
    ),
    "install": ("autoskillit.cli.install._marketplace", "install"),
    "upgrade": ("autoskillit.cli.install._marketplace", "upgrade"),
    "DefaultPluginRetirementCoordinator": (
        "autoskillit.cli.install._plugin_artifact",
        "DefaultPluginRetirementCoordinator",
    ),
    "InstalledPluginArtifactAuthority": (
        "autoskillit.cli.install._plugin_artifact",
        "InstalledPluginArtifactAuthority",
    ),
    "InstalledPluginArtifactRetirementOwner": (
        "autoskillit.cli.install._plugin_artifact",
        "InstalledPluginArtifactRetirementOwner",
    ),
    "current_installed_plugin_authority": (
        "autoskillit.cli.install._plugin_artifact",
        "current_installed_plugin_authority",
    ),
    "current_installed_plugin_root": (
        "autoskillit.cli.install._plugin_artifact",
        "current_installed_plugin_root",
    ),
    "default_plugin_retirement_coordinator": (
        "autoskillit.cli.install._plugin_artifact",
        "default_plugin_retirement_coordinator",
    ),
    "installed_plugin_artifact_lease_path": (
        "autoskillit.cli.install._plugin_artifact",
        "installed_plugin_artifact_lease_path",
    ),
    "installed_plugin_artifact_manifest_path": (
        "autoskillit.cli.install._plugin_artifact",
        "installed_plugin_artifact_manifest_path",
    ),
    "installed_plugin_semantic_key": (
        "autoskillit.cli.install._plugin_artifact",
        "installed_plugin_semantic_key",
    ),
    "interactive_plugin_authority": (
        "autoskillit.cli.install._plugin_artifact",
        "interactive_plugin_authority",
    ),
    "publish_installed_plugin_artifact": (
        "autoskillit.cli.install._plugin_artifact",
        "publish_installed_plugin_artifact",
    ),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module 'autoskillit.cli.install' has no attribute {name!r}")
    module_path, attr = target
    import importlib

    module = importlib.import_module(module_path)
    value = getattr(module, attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals().keys()) | set(_LAZY_ATTRS.keys()))
