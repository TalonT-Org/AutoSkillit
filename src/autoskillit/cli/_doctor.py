"""Doctor command implementation — project setup checks."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import Severity, is_git_worktree, pkg_root


@dataclass
class DoctorResult:
    severity: Severity
    check: str
    message: str


def _is_plugin_installed() -> bool:
    """Check if autoskillit is installed as a Claude Code plugin."""
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.is_file():
        return False
    try:
        data = json.loads(settings_path.read_text())
        enabled = data.get("enabledPlugins", {})
        return any(key.startswith("autoskillit@") for key in enabled if enabled[key])
    except (json.JSONDecodeError, AttributeError):
        return False


def run_doctor(*, output_json: bool = False, plugin_dir: str | None = None) -> None:
    """Check project setup for common issues."""
    results: list[DoctorResult] = []

    # Check 1: Stale MCP servers — dead binaries or nonexistent paths
    stale_servers: list[str] = []
    has_standalone_autoskillit = False
    claude_json = Path.home() / ".claude.json"
    if claude_json.is_file():
        data = json.loads(claude_json.read_text())
        servers = data.get("mcpServers", {})
        for name, entry in servers.items():
            if name == "autoskillit":
                has_standalone_autoskillit = True
                continue
            cmd = entry.get("command", "")
            if not cmd:
                continue
            cmd_path = Path(cmd)
            if cmd_path.is_absolute() and not cmd_path.exists():
                stale_servers.append(
                    f"MCP server '{name}' has dead command path: {cmd}. "
                    f"Remove with: claude mcp remove --scope user {name}"
                )
            elif not cmd_path.is_absolute() and shutil.which(cmd) is None:
                stale_servers.append(
                    f"MCP server '{name}' command not found: {cmd}. "
                    f"Remove with: claude mcp remove --scope user {name}"
                )
    if stale_servers:
        for msg in stale_servers:
            results.append(DoctorResult(Severity.ERROR, "stale_mcp_servers", msg))
    else:
        results.append(
            DoctorResult(Severity.OK, "stale_mcp_servers", "No stale MCP servers detected")
        )

    # Check 1b: Duplicate autoskillit — standalone entry alongside plugin install
    plugin_installed = _is_plugin_installed()
    if has_standalone_autoskillit and plugin_installed:
        results.append(
            DoctorResult(
                Severity.ERROR,
                "duplicate_mcp_server",
                "Standalone 'autoskillit' MCP server in ~/.claude.json duplicates "
                "the plugin registration. This spawns two server processes per session. "
                "Remove with: claude mcp remove autoskillit",
            )
        )
    elif has_standalone_autoskillit and not plugin_installed:
        results.append(
            DoctorResult(
                Severity.WARNING,
                "duplicate_mcp_server",
                "Standalone 'autoskillit' MCP server found in ~/.claude.json. "
                "Consider using 'autoskillit install' for persistent plugin registration instead.",
            )
        )
    else:
        results.append(
            DoctorResult(Severity.OK, "duplicate_mcp_server", "No duplicate MCP registrations")
        )

    # Check 2: Plugin metadata exists in package
    pkg_dir = pkg_root()
    if not (pkg_dir / ".claude-plugin" / "plugin.json").is_file():
        results.append(
            DoctorResult(
                Severity.ERROR,
                "plugin_metadata",
                "Plugin metadata missing. Reinstall autoskillit.",
            )
        )
    else:
        results.append(DoctorResult(Severity.OK, "plugin_metadata", "Plugin metadata exists"))

    # Check 3: autoskillit command on PATH
    if shutil.which("autoskillit") is None:
        results.append(
            DoctorResult(
                Severity.WARNING,
                "autoskillit_on_path",
                "'autoskillit' command not found on PATH.",
            )
        )
    else:
        results.append(
            DoctorResult(Severity.OK, "autoskillit_on_path", "autoskillit command found on PATH")
        )

    # Check 4: Config exists
    if not (Path.cwd() / ".autoskillit" / "config.yaml").is_file():
        results.append(
            DoctorResult(
                Severity.WARNING,
                "project_config",
                "No project config found. Run: autoskillit init",
            )
        )
    else:
        results.append(DoctorResult(Severity.OK, "project_config", "Project config exists"))

    # Check 5: Version consistency — plugin.json vs package version
    from autoskillit.version import version_info

    info = version_info(plugin_dir=plugin_dir)
    if info["plugin_json_version"] is None:
        results.append(
            DoctorResult(
                Severity.ERROR,
                "version_consistency",
                "Cannot verify version consistency: plugin.json not found. "
                "Reinstall: autoskillit install",
            )
        )
    elif not info["match"]:
        results.append(
            DoctorResult(
                Severity.ERROR,
                "version_consistency",
                f"Package version is {info['package_version']} but plugin.json "
                f"reports {info['plugin_json_version']}. "
                f"Update plugin.json or reinstall: autoskillit install",
            )
        )
    else:
        results.append(
            DoctorResult(
                Severity.OK,
                "version_consistency",
                f"Version {info['package_version']} consistent across package and plugin.json",
            )
        )

    # Check 6: Marketplace symlink freshness
    pkg_version = info["package_version"]
    marketplace_link = Path.home() / ".autoskillit" / "marketplace" / "plugins" / "autoskillit"
    if marketplace_link.is_symlink():
        target = marketplace_link.resolve()
        if not target.is_dir():
            results.append(
                DoctorResult(
                    Severity.ERROR,
                    "marketplace_freshness",
                    f"Marketplace symlink points to missing directory: {target}. "
                    f"Re-run: autoskillit install",
                )
            )
        elif is_git_worktree(target):
            results.append(
                DoctorResult(
                    Severity.ERROR,
                    "marketplace_freshness",
                    f"Marketplace symlink target is inside a git worktree: {target}\n"
                    "The symlink will break when the worktree is deleted.\n"
                    "Run 'autoskillit install' from the main project checkout to fix.",
                )
            )
        else:
            mkt_json = marketplace_link.parent.parent / ".claude-plugin" / "marketplace.json"
            if mkt_json.is_file():
                mkt_data = json.loads(mkt_json.read_text())
                plugins = mkt_data.get("plugins", [])
                mkt_version = plugins[0].get("version", "") if plugins else ""
                if mkt_version != pkg_version:
                    results.append(
                        DoctorResult(
                            Severity.WARNING,
                            "marketplace_freshness",
                            f"Marketplace manifest version ({mkt_version}) "
                            f"differs from installed version ({pkg_version}). "
                            f"Re-run: autoskillit install",
                        )
                    )
                else:
                    results.append(
                        DoctorResult(
                            Severity.OK,
                            "marketplace_freshness",
                            "Marketplace manifest version matches installed version",
                        )
                    )
    elif (Path.home() / ".autoskillit" / "marketplace").is_dir():
        results.append(
            DoctorResult(
                Severity.WARNING,
                "marketplace_freshness",
                "Marketplace symlink missing. Re-run: autoskillit install",
            )
        )

    # Check 8: Hook executability
    hooks_json_path = pkg_dir / "hooks" / "hooks.json"
    if hooks_json_path.is_file():
        hooks_data = json.loads(hooks_json_path.read_text())
        broken_hooks: list[str] = []
        for entry in hooks_data.get("hooks", {}).get("PreToolUse", []):
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                resolved = cmd.replace("${CLAUDE_PLUGIN_ROOT}", str(pkg_dir))
                parts = resolved.split()
                if len(parts) >= 2:
                    script_path = Path(parts[-1])
                    if not script_path.is_file():
                        broken_hooks.append(cmd)
        if broken_hooks:
            results.append(
                DoctorResult(
                    Severity.ERROR,
                    "hook_health",
                    f"Hook scripts not found: {', '.join(broken_hooks)}",
                )
            )
        else:
            results.append(DoctorResult(Severity.OK, "hook_health", "All hook scripts accessible"))
    else:
        results.append(
            DoctorResult(
                Severity.ERROR,
                "hook_health",
                "hooks.json not found — hook registration is broken",
            )
        )

    # Check 7: Script version health
    from autoskillit import __version__
    from autoskillit.core import RecipeSource
    from autoskillit.migration import FailureStore, default_store_path
    from autoskillit.recipe import list_recipes as _list_all_recipes

    _all_result = _list_all_recipes(Path.cwd())
    scripts_result_items = [r for r in _all_result.items if r.source == RecipeSource.PROJECT]
    if not scripts_result_items:
        results.append(
            DoctorResult(
                Severity.OK,
                "script_version_health",
                "No pipeline scripts found",
            )
        )
    else:
        from packaging.version import Version

        failure_store = FailureStore(default_store_path(Path.cwd()))
        known_failures = failure_store.load()

        failed_migrations: list[str] = []
        outdated: list[str] = []
        for script in scripts_result_items:
            if script.name in known_failures:
                f = known_failures[script.name]
                failed_migrations.append(
                    f"{script.name} (failed after {f.retries_attempted} retries)"
                )
            elif script.version is None or Version(script.version) < Version(__version__):
                outdated.append(script.name)

        if failed_migrations:
            results.append(
                DoctorResult(
                    Severity.ERROR,
                    "script_version_health",
                    "Migration failed — manual intervention required: "
                    + ", ".join(failed_migrations),
                )
            )
        elif outdated:
            results.append(
                DoctorResult(
                    Severity.WARNING,
                    "script_version_health",
                    "Outdated recipes: "
                    + ", ".join(outdated)
                    + ". Will be auto-migrated on next load.",
                )
            )
        else:
            results.append(
                DoctorResult(
                    Severity.OK,
                    "script_version_health",
                    "All recipes up to date",
                )
            )

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
        has_problems = any(r.severity != Severity.OK for r in results)
        if has_problems:
            for r in results:
                if r.severity != Severity.OK:
                    print(f"{r.severity.upper()}: {r.message}")
        else:
            for r in results:
                print(f"{r.severity}: {r.message}")
