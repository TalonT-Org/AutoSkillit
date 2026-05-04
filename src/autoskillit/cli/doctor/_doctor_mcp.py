"""MCP server registration and plugin cache doctor checks."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from autoskillit.core import DIRECT_INSTALL_CACHE_SUBDIR, Severity, build_claude_env, get_logger

from ._doctor_types import DoctorResult

logger = get_logger(__name__)


def _check_stale_mcp_servers(claude_json_path: Path | None = None) -> list[DoctorResult]:
    """Check ~/.claude.json for stale autoskillit* MCP server entries with dead paths."""
    _path = claude_json_path or (Path.home() / ".claude.json")
    if not _path.is_file():
        return [DoctorResult(Severity.OK, "stale_mcp_servers", "No stale MCP servers detected")]

    try:
        data = json.loads(_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return [
            DoctorResult(
                Severity.ERROR,
                "stale_mcp_servers",
                f"~/.claude.json could not be parsed: {exc}",
            )
        ]

    servers = data.get("mcpServers", {})
    stale_msgs: list[str] = []
    for name, entry in servers.items():
        cmd = entry.get("command", "")
        if not cmd:
            continue
        cmd_path = Path(cmd)
        if cmd_path.is_absolute() and not cmd_path.exists():
            stale_msgs.append(
                f"MCP server '{name}' has dead command path: {cmd}. "
                f"Remove with: claude mcp remove --scope user {name}"
            )
        elif not cmd_path.is_absolute() and shutil.which(cmd) is None:
            stale_msgs.append(
                f"MCP server '{name}' command not found: {cmd}. "
                f"Remove with: claude mcp remove --scope user {name}"
            )

    if stale_msgs:
        return [DoctorResult(Severity.ERROR, "stale_mcp_servers", msg) for msg in stale_msgs]
    return [DoctorResult(Severity.OK, "stale_mcp_servers", "No stale MCP servers detected")]


def _check_mcp_server_registered(claude_json_path: Path | None = None) -> DoctorResult:
    """Check that autoskillit MCP server is registered (via mcpServers or plugin)."""
    if claude_json_path is None:
        claude_json_path = Path.home() / ".claude.json"

    if claude_json_path.exists():
        try:
            data = json.loads(claude_json_path.read_text())
            if "autoskillit" in data.get("mcpServers", {}):
                return DoctorResult(
                    severity=Severity.OK,
                    check="mcp_server_registered",
                    message="autoskillit registered in mcpServers",
                )
        except OSError as exc:
            return DoctorResult(
                severity=Severity.ERROR,
                check="mcp_server_registered",
                message=f"~/.claude.json could not be read: {exc}",
            )
        except json.JSONDecodeError as exc:
            return DoctorResult(
                severity=Severity.ERROR,
                check="mcp_server_registered",
                message=f"~/.claude.json is not valid JSON: {exc}",
            )

    _plugin_check_detail = ""
    try:
        result = subprocess.run(
            ["claude", "plugin", "list"],
            capture_output=True,
            text=True,
            timeout=10,
            env=build_claude_env(),
        )
        if result.returncode == 0 and "autoskillit" in result.stdout:
            return DoctorResult(
                severity=Severity.OK,
                check="mcp_server_registered",
                message="autoskillit registered as Claude plugin",
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        _plugin_check_detail = f" (claude plugin list unavailable: {type(exc).__name__})"
    else:
        _plugin_check_detail = ""

    return DoctorResult(
        severity=Severity.WARNING,
        check="mcp_server_registered",
        message=(
            "autoskillit not registered. Run 'autoskillit install' to install as a plugin, "
            "or 'autoskillit init' to register in mcpServers." + _plugin_check_detail
        ),
    )


def _check_dual_mcp_registration(
    claude_json_path: Path | None = None,
    plugins_json_path: Path | None = None,
) -> DoctorResult:
    """Check that autoskillit is not registered both as a direct entry and as a marketplace plugin.

    Returns WARNING if both registrations are simultaneously present (split-brain condition).
    Fail-open: unreadable files → cannot confirm dual registration, return OK.
    """
    from autoskillit.cli._init_helpers import _check_dual_mcp_files

    _claude_json = claude_json_path or (Path.home() / ".claude.json")
    _plugins_json = plugins_json_path or (
        Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    )
    if _check_dual_mcp_files(_claude_json, _plugins_json):
        return DoctorResult(
            severity=Severity.WARNING,
            check="dual_mcp_registration",
            message=(
                "autoskillit is registered both as a direct mcpServers entry "
                "(~/.claude.json) and as a marketplace plugin. This spawns two "
                "independent server processes per session with split gate state. "
                "Run `autoskillit install` to remove the stale direct entry."
            ),
        )
    return DoctorResult(
        severity=Severity.OK,
        check="dual_mcp_registration",
        message="",
    )


def _check_plugin_cache_exists(cache_dir: Path | None = None) -> DoctorResult:
    """Check that the plugin cache directory exists."""
    from autoskillit.cli._install_info import InstallType, detect_install

    info = detect_install()
    if info.install_type == InstallType.LOCAL_EDITABLE:
        return DoctorResult(
            Severity.OK,
            "plugin_cache_exists",
            "Plugin cache check skipped (editable dev install)",
        )

    _cache_dir = cache_dir or (
        Path.home() / ".claude" / "plugins" / "cache" / DIRECT_INSTALL_CACHE_SUBDIR / "autoskillit"
    )
    if _cache_dir.is_dir():
        return DoctorResult(
            Severity.OK,
            "plugin_cache_exists",
            "Plugin cache directory exists",
        )
    return DoctorResult(
        Severity.WARNING,
        "plugin_cache_exists",
        f"Plugin cache directory missing: {_cache_dir}. Run `autoskillit install` to recreate it.",
    )


def _check_installed_plugins_entry(plugins_json_path: Path | None = None) -> DoctorResult:
    """Check that installed_plugins.json contains the autoskillit entry."""
    from autoskillit.cli._installed_plugins import InstalledPluginsFile

    store = InstalledPluginsFile(plugins_json_path)
    if not store.path.exists():
        return DoctorResult(
            Severity.WARNING,
            "installed_plugins_entry",
            "installed_plugins.json not found. Run `autoskillit install`.",
        )
    if store.contains("autoskillit@autoskillit-local"):
        return DoctorResult(
            Severity.OK,
            "installed_plugins_entry",
            "autoskillit entry present in installed_plugins.json",
        )
    return DoctorResult(
        Severity.WARNING,
        "installed_plugins_entry",
        "autoskillit entry missing from installed_plugins.json. Run `autoskillit install` to fix.",
    )


def _check_plugin_cache_integrity(cache_dir: Path | None = None) -> DoctorResult:
    """Validate that plugin cache hooks.json paths resolve to real files."""
    from autoskillit.hook_registry import validate_plugin_cache_hooks

    broken = validate_plugin_cache_hooks(cache_dir=cache_dir)
    if broken:
        broken_str = ", ".join(broken)
        return DoctorResult(
            Severity.ERROR,
            "plugin_cache_integrity",
            f"Plugin cache hooks.json has {len(broken)} broken path(s): {broken_str}. "
            f"Run `autoskillit install` to rebuild the cache.",
        )
    return DoctorResult(
        Severity.OK,
        "plugin_cache_integrity",
        "Plugin cache hook paths are valid",
    )


def _check_cache_version_mismatch(cache_dir: Path | None = None) -> DoctorResult:
    """Check plugin cache version. ERROR if kitchen is open and versions mismatch."""
    from autoskillit.core import any_kitchen_open
    from autoskillit.version import version_info

    _cache_plugin_dir = cache_dir or (
        Path.home() / ".claude" / "plugins" / "cache" / DIRECT_INSTALL_CACHE_SUBDIR / "autoskillit"
    )
    try:
        vi = version_info(plugin_dir=str(_cache_plugin_dir))
    except Exception as exc:
        logger.warning("version_info_failed", plugin_dir=str(_cache_plugin_dir), exc_info=True)
        return DoctorResult(
            Severity.ERROR,
            "version_consistency",
            f"Could not read plugin cache version info: {exc}. "
            "Run `autoskillit install` to rebuild.",
        )
    if vi["match"]:
        return DoctorResult(
            Severity.OK,
            "version_consistency",
            f"Version {vi['package_version']} — plugin cache is current",
        )
    mismatch_msg = (
        f"Plugin cache version {vi['plugin_json_version']!r} does not match "
        f"installed package {vi['package_version']!r}. "
        f"Run 'autoskillit install' to sync."
    )
    if any_kitchen_open(project_path=str(Path.cwd())):
        return DoctorResult(
            Severity.ERROR,
            "version_consistency",
            mismatch_msg + " Kitchen is open — tool calls may fail with ENOENT until kitchens are "
            "closed and `autoskillit install` is re-run.",
        )
    return DoctorResult(
        Severity.WARNING,
        "version_consistency",
        mismatch_msg,
    )
