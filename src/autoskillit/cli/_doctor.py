"""Doctor command implementation — project setup checks."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from autoskillit.cli._hooks import _claude_settings_path, _load_settings_data
from autoskillit.core import Severity
from autoskillit.hook_registry import HOOK_REGISTRY


@dataclass
class DoctorResult:
    """Outcome of a single doctor check.

    The ``fix`` field is for **external programmatic callers** that want to
    inspect results and apply remediation themselves.  ``run_doctor`` does not
    dispatch ``fix`` — it passes ``fix=True`` directly into each check function,
    which applies the fix inline and returns an ``OK`` result immediately.
    External callers (e.g. tests or integrations) may call ``result.fix()``
    after receiving an ``ERROR`` result from a check function invoked with
    ``fix=False``.
    """

    severity: Severity
    check: str
    message: str
    fix: Callable[[], None] | None = field(default=None, repr=False)


def _check_mcp_server_registered() -> DoctorResult:
    claude_json = Path.home() / ".claude.json"
    if not claude_json.exists():
        return DoctorResult(
            severity=Severity.WARNING,
            check="mcp_server_registered",
            message="~/.claude.json not found. Run 'autoskillit init' to register.",
        )
    try:
        data = json.loads(claude_json.read_text())
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
    if "autoskillit" not in data.get("mcpServers", {}):
        return DoctorResult(
            severity=Severity.WARNING,
            check="mcp_server_registered",
            message="autoskillit not in ~/.claude.json mcpServers. Run 'autoskillit init'.",
        )
    return DoctorResult(
        severity=Severity.OK,
        check="mcp_server_registered",
        message="autoskillit registered in ~/.claude.json mcpServers.",
    )


def _check_hook_registration(settings_path: Path) -> DoctorResult:
    data = _load_settings_data(settings_path)
    registered = " ".join(
        hook.get("command", "")
        for entry in data.get("hooks", {}).get("PreToolUse", [])
        for hook in entry.get("hooks", [])
    )
    missing = [
        script for hdef in HOOK_REGISTRY for script in hdef.scripts if script not in registered
    ]
    if missing:
        return DoctorResult(
            severity=Severity.WARNING,
            check="hook_registration",
            message=f"Missing hooks: {', '.join(missing)}. Run 'autoskillit init'.",
        )
    return DoctorResult(
        severity=Severity.OK,
        check="hook_registration",
        message="All HOOK_REGISTRY scripts present in settings.json.",
    )


def run_doctor(*, output_json: bool = False, fix: bool = False) -> None:
    """Check project setup for common issues."""
    results: list[DoctorResult] = []

    # Check 1: Stale MCP servers — dead binaries or nonexistent paths
    stale_servers: list[str] = []
    _stale_parse_error = False
    claude_json = Path.home() / ".claude.json"
    if claude_json.is_file():
        try:
            data = json.loads(claude_json.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            results.append(
                DoctorResult(
                    Severity.ERROR,
                    "stale_mcp_servers",
                    f"~/.claude.json could not be parsed: {exc}",
                )
            )
            _stale_parse_error = True
            data = {}
        servers = data.get("mcpServers", {})
        for name, entry in servers.items():
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
    if not _stale_parse_error:
        if stale_servers:
            for msg in stale_servers:
                results.append(DoctorResult(Severity.ERROR, "stale_mcp_servers", msg))
        else:
            results.append(
                DoctorResult(Severity.OK, "stale_mcp_servers", "No stale MCP servers detected")
            )

    # Check 2: MCP server registered in ~/.claude.json
    results.append(_check_mcp_server_registered())

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

    # Check 5: Version consistency — package version must match plugin.json
    from autoskillit.version import version_info

    vi = version_info()
    if vi["match"]:
        results.append(
            DoctorResult(
                Severity.OK,
                "version_consistency",
                f"Version {vi['package_version']} installed",
            )
        )
    else:
        results.append(
            DoctorResult(
                Severity.WARNING,
                "version_consistency",
                f"Package version {vi['package_version']} does not match "
                f"plugin.json {vi['plugin_json_version']}. Reinstall autoskillit to fix.",
            )
        )

    # Check 6: Hook executability — validates scripts from the canonical registry
    from autoskillit.hooks import generate_hooks_json

    hooks_data = generate_hooks_json()
    broken_hooks: list[str] = []
    for entry in hooks_data.get("hooks", {}).get("PreToolUse", []):
        for hook in entry.get("hooks", []):
            cmd = hook.get("command", "")
            parts = cmd.split()
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

    # Check 7: Hook registration in settings.json
    results.append(_check_hook_registration(_claude_settings_path("user")))

    # Check 8: Script version health
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
