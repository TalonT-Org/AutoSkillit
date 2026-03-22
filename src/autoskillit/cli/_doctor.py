"""Doctor command implementation — project setup checks."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from autoskillit.cli._hooks import _claude_settings_path, _load_settings_data
from autoskillit.cli._init_helpers import _KNOWN_SCANNERS, _detect_secret_scanner
from autoskillit.core import _ROOT_GITIGNORE_ENTRIES, Severity
from autoskillit.hook_registry import HOOK_REGISTRY


@dataclass
class DoctorResult:
    """Outcome of a single doctor check."""

    severity: Severity
    check: str
    message: str


def _check_mcp_server_registered(claude_json_path: Path | None = None) -> DoctorResult:
    """Check that autoskillit MCP server is registered (via mcpServers or plugin)."""
    import subprocess

    if claude_json_path is None:
        claude_json_path = Path.home() / ".claude.json"

    # Check 1: direct mcpServers entry (legacy / init-based registration)
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

    # Check 2: plugin-based registration (install-based)
    _plugin_check_detail = ""
    try:
        result = subprocess.run(
            ["claude", "plugin", "list"],
            capture_output=True,
            text=True,
            timeout=10,
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


def _check_hook_registration(settings_path: Path) -> DoctorResult:
    data = _load_settings_data(settings_path)
    registered = " ".join(
        hook.get("command", "")
        for event_entries in data.get("hooks", {}).values()
        if isinstance(event_entries, list)
        for entry in event_entries
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


def _check_gitignore_completeness(project_dir: Path) -> DoctorResult:
    """Check that every file in .autoskillit/ is gitignored or in the committed allowlist."""
    from autoskillit.core import _AUTOSKILLIT_GITIGNORE_ENTRIES, _COMMITTED_BY_DESIGN

    autoskillit_dir = project_dir / ".autoskillit"
    gitignore_path = autoskillit_dir / ".gitignore"
    if not autoskillit_dir.is_dir():
        return DoctorResult(Severity.OK, "gitignore_completeness", "No .autoskillit/ directory.")
    if not gitignore_path.exists():
        return DoctorResult(
            Severity.WARNING,
            "gitignore_completeness",
            ".autoskillit/.gitignore missing. Run 'autoskillit init'.",
        )
    gitignore_content = gitignore_path.read_text(encoding="utf-8")
    uncovered: list[str] = []
    for item in sorted(autoskillit_dir.iterdir()):
        if item.name == ".gitignore":
            continue
        if item.name in _COMMITTED_BY_DESIGN:
            continue
        check_name = item.name + "/" if item.is_dir() else item.name
        if check_name not in gitignore_content:
            uncovered.append(item.name)
    # Also check that every entry in the canonical list is present
    for entry in _AUTOSKILLIT_GITIGNORE_ENTRIES:
        if entry not in gitignore_content:
            entry_name = entry.rstrip("/")
            if entry_name not in uncovered:
                uncovered.append(entry_name)
    root_gitignore = project_dir / ".gitignore"
    root_content = root_gitignore.read_text(encoding="utf-8") if root_gitignore.exists() else ""
    for entry in _ROOT_GITIGNORE_ENTRIES:
        if entry not in root_content:
            entry_name = entry.rstrip("/")
            if entry_name not in uncovered:
                uncovered.append(entry_name)
    if uncovered:
        return DoctorResult(
            Severity.WARNING,
            "gitignore_completeness",
            f"Files in .autoskillit/ not covered by .gitignore: {', '.join(uncovered)}. "
            "Add to _AUTOSKILLIT_GITIGNORE_ENTRIES or _COMMITTED_BY_DESIGN.",
        )
    return DoctorResult(Severity.OK, "gitignore_completeness", "All .autoskillit/ files covered.")


def _check_secret_scanning_hook(project_dir: Path) -> DoctorResult:
    """Check that .pre-commit-config.yaml includes a known secret scanning hook."""
    if _detect_secret_scanner(project_dir):
        return DoctorResult(
            Severity.OK,
            "secret_scanning_hook",
            "Secret scanning hook detected in .pre-commit-config.yaml.",
        )
    pre_commit_path = project_dir / ".pre-commit-config.yaml"
    if not pre_commit_path.exists():
        msg = (
            "No .pre-commit-config.yaml found. AutoSkillit commits code automatically — "
            "add a secret scanner (gitleaks, detect-secrets, trufflehog, or git-secrets) "
            "to prevent credential leaks."
        )
    else:
        scanners = ", ".join(sorted(_KNOWN_SCANNERS))
        msg = (
            f".pre-commit-config.yaml exists but contains no known secret scanner "
            f"({scanners}). Add one to prevent credential leaks."
        )
    return DoctorResult(Severity.ERROR, "secret_scanning_hook", msg)


def _check_config_layers_for_secrets(
    project_dir: Path | None = None,
) -> DoctorResult:
    """Check all config.yaml layers for _SECRETS_ONLY_KEYS violations.

    Scans the user-level and project-level config.yaml files for any keys
    that belong only in .secrets.yaml. Reports ERROR with exact fix guidance.
    """
    from autoskillit.config import ConfigSchemaError, validate_layer_keys
    from autoskillit.core import YAMLError, load_yaml

    root = project_dir or Path.cwd()
    config_paths = [
        Path.home() / ".autoskillit" / "config.yaml",
        root / ".autoskillit" / "config.yaml",
    ]
    for config_path in config_paths:
        if not config_path.is_file():
            continue
        try:
            data = load_yaml(config_path) or {}
        except YAMLError as exc:
            return DoctorResult(
                severity=Severity.WARNING,
                check="config_secrets_placement",
                message=f"Could not parse {str(config_path)!r} as YAML: {exc}",
            )
        if not isinstance(data, dict):
            continue
        try:
            validate_layer_keys(data, config_path, is_secrets_layer=False)
        except ConfigSchemaError as exc:
            return DoctorResult(
                severity=Severity.ERROR,
                check="config_secrets_placement",
                message=str(exc),
            )
    return DoctorResult(
        severity=Severity.OK,
        check="config_secrets_placement",
        message="No secrets found in config.yaml layers",
    )


def run_doctor(*, output_json: bool = False) -> None:
    """Check project setup for common issues."""
    from autoskillit.cli._marketplace import _clear_plugin_cache

    _clear_plugin_cache()

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

    # Check 2: MCP server registered in ~/.claude.json or via plugin
    results.append(_check_mcp_server_registered(claude_json_path=Path.home() / ".claude.json"))

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

    # Check 4b: Config secrets placement
    results.append(_check_config_layers_for_secrets())

    # Check 5: Version consistency — package version must match plugin.json
    import importlib.metadata
    import importlib.resources as _ir

    pkg_version = importlib.metadata.version("autoskillit")
    plugin_json_path = Path(str(_ir.files("autoskillit"))) / ".claude-plugin" / "plugin.json"
    try:
        plugin_version = json.loads(plugin_json_path.read_text()).get("version")
    except (json.JSONDecodeError, OSError):
        plugin_version = None
    if plugin_version == pkg_version:
        results.append(
            DoctorResult(
                Severity.OK,
                "version_consistency",
                f"Version {pkg_version} installed",
            )
        )
    else:
        results.append(
            DoctorResult(
                Severity.WARNING,
                "version_consistency",
                f"Package version {pkg_version} does not match "
                f"plugin.json {plugin_version!r}. Reinstall autoskillit to fix.",
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

    # Check 9: gitignore completeness
    results.append(_check_gitignore_completeness(Path.cwd()))

    # Check 10: Secret scanning hook
    results.append(_check_secret_scanning_hook(Path.cwd()))

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
