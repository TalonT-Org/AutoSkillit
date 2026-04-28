"""Doctor command implementation — project setup checks."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.cli._hooks import _claude_settings_path
from autoskillit.config import load_config
from autoskillit.core import Severity, get_logger, is_feature_enabled
from autoskillit.hook_registry import iter_all_scope_paths

from ._doctor_config import (
    _check_config_layers_for_secrets,
    _check_gitignore_completeness,
    _check_project_config,
    _check_script_version_health,
    _check_secret_scanning_hook,
)
from ._doctor_env import (
    _check_ambient_campaign_id,
    _check_ambient_session_type_fleet,
    _check_ambient_session_type_leaf,
    _check_ambient_session_type_orchestrator,
)
from ._doctor_features import (
    _check_feature_dependencies,
    _check_feature_registry_consistency,
)
from ._doctor_fleet import (
    _check_campaign_manifest_clone_dests,
    _check_campaign_onboarding_hint,
    _check_fleet_dispatch_guard_registered,
    _check_sous_chef_bundled,
    _check_stale_fleet_state,
)
from ._doctor_hooks import (
    _check_hook_health_all_scopes,
    _check_hook_registration,
    _check_hook_registry_drift,
)
from ._doctor_install import (
    _check_autoskillit_on_path,
    _check_editable_install_source_exists,
    _check_install_classification,
    _check_source_version_drift,
    _check_stale_entry_points,
    _check_update_dismissal_state,
)
from ._doctor_mcp import (
    _check_dual_mcp_registration,
    _check_installed_plugins_entry,
    _check_mcp_server_registered,
    _check_plugin_cache_exists,
    _check_stale_mcp_servers,
)
from ._doctor_runtime import (
    _check_claude_process_state_breakdown,
    _check_quota_cache_schema,
)
from ._doctor_types import _NON_PROBLEM, DoctorResult

logger = get_logger(__name__)

__all__ = ["DoctorResult", "run_doctor"]


def run_doctor(*, output_json: bool = False) -> None:
    """Check project setup for common issues."""
    cfg = load_config(Path.cwd())
    results: list[DoctorResult] = []

    # Check 1: Stale MCP servers — dead binaries or nonexistent paths
    results.extend(_check_stale_mcp_servers(Path.home() / ".claude.json"))

    # Check 2: MCP server registered in ~/.claude.json or via plugin
    results.append(_check_mcp_server_registered(claude_json_path=Path.home() / ".claude.json"))

    # Check 2b: Dual MCP registration — direct entry and marketplace plugin both present
    results.append(_check_dual_mcp_registration())

    # Check 2c: Plugin cache directory exists
    results.append(_check_plugin_cache_exists())

    # Check 2d: installed_plugins.json has autoskillit entry
    results.append(_check_installed_plugins_entry())

    # Check 3: autoskillit command on PATH
    results.append(_check_autoskillit_on_path())

    # Check 4: Config exists
    results.append(_check_project_config())

    # Check 4b: Config secrets placement
    results.append(_check_config_layers_for_secrets())

    # Check 5: Version consistency — cached plugin.json must match installed package
    from autoskillit.version import version_info as _version_info

    _cache_plugin_dir = (
        Path.home() / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
    )
    vi = _version_info(plugin_dir=str(_cache_plugin_dir))
    if vi["match"]:
        results.append(
            DoctorResult(
                Severity.OK,
                "version_consistency",
                f"Version {vi['package_version']} — plugin cache is current",
            )
        )
    else:
        results.append(
            DoctorResult(
                Severity.WARNING,
                "version_consistency",
                f"Plugin cache version {vi['plugin_json_version']!r} does not match "
                f"installed package {vi['package_version']!r}. "
                f"Run 'autoskillit install' to sync.",
            )
        )

    # Check 6: Hook executability — validates deployed scripts for all event types (all scopes)
    results.extend(_check_hook_health_all_scopes(Path.cwd()))

    # Check 7: Hook registration in settings.json
    results.append(_check_hook_registration(_claude_settings_path("user")))

    # Check 7b: Hook registry drift (multi-scope)
    for scope_label, settings_path in iter_all_scope_paths(Path.cwd()):
        results.append(_check_hook_registry_drift(settings_path, scope_label=scope_label))

    # Check 8: Script version health
    results.append(_check_script_version_health())

    # Check 9: gitignore completeness
    results.append(_check_gitignore_completeness(Path.cwd()))

    # Check 10: Secret scanning hook
    results.append(_check_secret_scanning_hook(Path.cwd()))

    # Check 11: Editable install source directory still exists
    results.append(_check_editable_install_source_exists())

    # Check 12: No stale autoskillit entry points outside ~/.local/bin
    results.append(_check_stale_entry_points())

    # Check 13: Source version drift (network, with disk-cache TTL fallback)
    results.append(_check_source_version_drift())

    # Check 14: Quota cache schema version
    results.append(_check_quota_cache_schema())

    # Check 15: claude process state breakdown
    results.append(_check_claude_process_state_breakdown())

    # Check 16: Install classification from direct_url.json
    results.append(_check_install_classification())

    # Check 17: Update-prompt dismissal state
    results.append(_check_update_dismissal_state())

    # -- Fleet doctor checks (ambient env + infrastructure health) --

    # Check 18: Ambient SESSION_TYPE=leaf leak detection
    results.append(_check_ambient_session_type_leaf())

    # Check 19: Ambient SESSION_TYPE=orchestrator leak detection
    results.append(_check_ambient_session_type_orchestrator())

    # Check 20: Ambient SESSION_TYPE=fleet leak detection
    results.append(_check_ambient_session_type_fleet())

    # Check 21: Ambient CAMPAIGN_ID leak detection
    results.append(_check_ambient_campaign_id())

    # Check 22: Feature dependency consistency
    results.append(_check_feature_dependencies(cfg.features))

    # Check 23: Feature registry import consistency
    results.append(_check_feature_registry_consistency())

    # Checks 24–28: Fleet infrastructure — only when fleet feature is enabled
    if is_feature_enabled("fleet", cfg.features, experimental_enabled=cfg.experimental_enabled):
        # Check 24: Sous-chef skill directory exists
        results.append(_check_sous_chef_bundled())

        # Check 25: Fleet dispatch guard registered
        results.append(_check_fleet_dispatch_guard_registered())

        # Check 26: Stale fleet state (running > 7 days)
        results.append(_check_stale_fleet_state())

        # Check 27: Campaign onboarding hint
        results.append(_check_campaign_onboarding_hint())

        # Check 28: Campaign manifest clone destination collisions
        results.append(_check_campaign_manifest_clone_dests())

    # Output
    if output_json:
        print(
            json.dumps(
                {
                    "results": [
                        {"severity": r.severity, "check": r.check, "message": r.message}
                        for r in results
                    ]
                },
                indent=2,
            )
        )
    else:
        has_problems = any(r.severity not in _NON_PROBLEM for r in results)
        if has_problems:
            for r in results:
                if r.severity not in _NON_PROBLEM:
                    print(f"{r.severity.upper()}: {r.message}")
        else:
            for r in results:
                print(f"{r.severity}: {r.message}")
