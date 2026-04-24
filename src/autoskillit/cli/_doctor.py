"""Doctor command implementation — project setup checks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from autoskillit.cli._hooks import _claude_settings_path, _load_settings_data
from autoskillit.cli._init_helpers import (
    _KNOWN_SCANNERS,
    _check_dual_mcp_files,
    _detect_secret_scanner,
)
from autoskillit.config import load_config
from autoskillit.core import (
    SESSION_TYPE_ENV_VAR,
    Severity,
    get_logger,
    is_feature_enabled,
    pkg_root,
)
from autoskillit.execution import QUOTA_CACHE_SCHEMA_VERSION
from autoskillit.hook_registry import (
    _count_hook_registry_drift,
    canonical_script_basenames,
    find_broken_hook_scripts,
)

_log = get_logger(__name__)
_NON_PROBLEM: frozenset[Severity] = frozenset({Severity.OK, Severity.INFO})
_STALE_THRESHOLD_DAYS = 7


@dataclass
class DoctorResult:
    """Outcome of a single doctor check."""

    severity: Severity
    check: str
    message: str


def _check_mcp_server_registered(claude_json_path: Path | None = None) -> DoctorResult:
    """Check that autoskillit MCP server is registered (via mcpServers or plugin)."""
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


def _check_dual_mcp_registration(
    claude_json_path: Path | None = None,
    plugins_json_path: Path | None = None,
) -> DoctorResult:
    """Check that autoskillit is not registered both as a direct entry and as a marketplace plugin.

    Returns WARNING if both registrations are simultaneously present (split-brain condition).
    Fail-open: unreadable files → cannot confirm dual registration, return OK.
    """
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


def _check_hook_registration(settings_path: Path) -> DoctorResult:
    data = _load_settings_data(settings_path)
    registered = " ".join(
        hook.get("command", "")
        for event_entries in data.get("hooks", {}).values()
        if isinstance(event_entries, list)
        for entry in event_entries
        for hook in entry.get("hooks", [])
    )
    missing = [script for script in canonical_script_basenames() if script not in registered]
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


def _check_hook_registry_drift(
    settings_path: Path, scope_label: str | None = None
) -> DoctorResult:
    """Compare generate_hooks_json() with what is deployed in settings.json."""
    result = _count_hook_registry_drift(settings_path)
    if result.orphaned > 0:
        ghost_scripts = sorted(result.orphaned_cmds)
        msg = (
            f"Orphaned hook entries detected: {', '.join(ghost_scripts)}. "
            f"These scripts are missing from HOOK_REGISTRY but present in "
            f"settings.json — every matching tool call will be denied with ENOENT. "
            f"Run 'autoskillit install' to regenerate hooks."
        )
        if scope_label:
            msg = f"[{scope_label}] {msg}"
        return DoctorResult(Severity.ERROR, "hook_registry_drift", msg)
    if result.missing > 0:
        msg = (
            f"Hook registry has changed since last install. "
            f"Run 'autoskillit install' to deploy {result.missing} new/changed hook(s)."
        )
        if scope_label:
            msg = f"[{scope_label}] {msg}"
        return DoctorResult(
            severity=Severity.WARNING,
            check="hook_registry_drift",
            message=msg,
        )
    msg = "Deployed hooks match HOOK_REGISTRY."
    if scope_label:
        msg = f"[{scope_label}] {msg}"
    return DoctorResult(
        severity=Severity.OK,
        check="hook_registry_drift",
        message=msg,
    )


def _check_hook_health(settings_path: Path) -> DoctorResult:
    """Verify all deployed hook scripts exist on disk for all event types (single scope)."""
    broken_hooks = find_broken_hook_scripts(settings_path)
    if broken_hooks:
        return DoctorResult(
            severity=Severity.ERROR,
            check="hook_health",
            message=f"Hook scripts not found: {', '.join(broken_hooks)}",
        )
    return DoctorResult(Severity.OK, "hook_health", "All hook scripts accessible")


def _check_hook_health_all_scopes(project_root: Path | None = None) -> list[DoctorResult]:
    """Verify all deployed hook scripts exist on disk across ALL scopes."""
    from autoskillit.hook_registry import iter_all_scope_paths

    results: list[DoctorResult] = []
    for scope_label, settings_path in iter_all_scope_paths(project_root):
        broken = find_broken_hook_scripts(settings_path)
        if broken:
            results.append(
                DoctorResult(
                    severity=Severity.ERROR,
                    check="hook_health",
                    message=f"[{scope_label}] Hook scripts not found: {', '.join(broken)}",
                )
            )
    if not results:
        results.append(
            DoctorResult(Severity.OK, "hook_health", "All hook scripts accessible (all scopes)")
        )
    return results


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


def _check_editable_install_source_exists() -> DoctorResult:
    """Detect editable autoskillit installs whose source directory no longer exists."""
    import importlib.metadata as meta

    check_name = "editable_install_source_exists"
    try:
        dist = meta.Distribution.from_name("autoskillit")
    except meta.PackageNotFoundError:
        return DoctorResult(Severity.OK, check_name, "autoskillit not installed in this env")

    direct_url_text = dist.read_text("direct_url.json")
    if not direct_url_text:
        return DoctorResult(Severity.OK, check_name, "Not an editable install")

    try:
        direct_url = json.loads(direct_url_text)
    except json.JSONDecodeError:
        return DoctorResult(Severity.OK, check_name, "direct_url.json unreadable — skipped")

    is_editable = (
        direct_url.get("dir_info", {}).get("editable") is True
        or direct_url.get("editable") is True
    )
    if not is_editable:
        return DoctorResult(Severity.OK, check_name, "Not an editable install")

    url = direct_url.get("url", "")
    src_path = urllib.parse.urlparse(url).path if url.startswith("file://") else ""
    if not src_path or Path(src_path).exists():
        return DoctorResult(Severity.OK, check_name, "Editable install source directory exists")

    return DoctorResult(
        Severity.ERROR,
        check_name,
        f"autoskillit is installed from a deleted directory: {src_path}. "
        f"Fix: uv tool install --force autoskillit && autoskillit install",
    )


def _check_stale_entry_points() -> DoctorResult:
    """Detect autoskillit binaries on PATH outside ~/.local/bin (stale/poisoned installs)."""
    check_name = "stale_entry_points"
    primary = shutil.which("autoskillit")
    if not primary:
        return DoctorResult(Severity.OK, check_name, "autoskillit not found on PATH")

    try:
        result = subprocess.run(
            ["which", "-a", "autoskillit"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        all_paths = [p.strip() for p in result.stdout.splitlines() if p.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        all_paths = [primary]

    expected_prefix = Path.home() / ".local"
    stale = [p for p in all_paths if not Path(p).is_relative_to(expected_prefix)]
    if not stale:
        return DoctorResult(Severity.OK, check_name, "No stale autoskillit entry points found")

    stale_list = ", ".join(stale)
    return DoctorResult(
        Severity.WARNING,
        check_name,
        f"Found autoskillit entry point(s) outside ~/.local/bin: {stale_list}. "
        f"These may be stale editable installs. "
        f"Fix: uv tool install --force autoskillit && autoskillit install",
    )


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


def _check_source_version_drift(home: Path | None = None) -> DoctorResult:
    """Network source-drift check.

    Compares the installed commit SHA against the current HEAD of the branch
    the binary was installed from.  Uses a network request to get the latest
    SHA (with disk-cache TTL fallback).
    """
    check_name = "source_version_drift"
    _home = home or Path.home()

    try:
        from autoskillit.cli._install_info import InstallType, detect_install
        from autoskillit.cli._update_checks import resolve_reference_sha

        info = detect_install()

        if info.install_type == InstallType.LOCAL_EDITABLE:
            return DoctorResult(
                Severity.OK, check_name, "Local editable install — drift check not applicable"
            )

        if info.install_type in (InstallType.UNKNOWN, InstallType.LOCAL_PATH):
            return DoctorResult(
                Severity.OK,
                check_name,
                "Not a source-tracked install — drift check not applicable",
            )

        # GIT_VCS: resolve SHA via network (with disk-cache fallback)
        ref_sha = resolve_reference_sha(info, _home, network=True)

        if ref_sha is None:
            return DoctorResult(
                Severity.OK,
                check_name,
                "Source drift reference SHA unavailable — check network connectivity",
            )

        if info.commit_id == ref_sha:
            return DoctorResult(Severity.OK, check_name, "No source drift detected")

        installed_short = (info.commit_id or "unknown")[:8]
        ref_short = ref_sha[:8]
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"Source drift: installed={installed_short}, reference={ref_short}. "
            f"Run the appropriate install command to update.",
        )

    except Exception:
        _log.debug("Source drift check failed", exc_info=True)
        return DoctorResult(
            Severity.OK, check_name, "Source drift check skipped (unexpected error)"
        )


def _check_install_classification() -> DoctorResult:
    """Classify the current autoskillit install type via direct_url.json."""
    check_name = "install_classification"
    try:
        from autoskillit.cli._install_info import InstallType, detect_install

        info = detect_install()
        if info.install_type == InstallType.UNKNOWN:
            return DoctorResult(
                Severity.WARNING,
                check_name,
                "install type could not be detected from direct_url.json",
            )
        commit_short = (info.commit_id or "")[:8]
        return DoctorResult(
            Severity.OK,
            check_name,
            f"install_type={info.install_type}, "
            f"requested_revision={info.requested_revision}, "
            f"commit_id={commit_short}",
        )
    except Exception:
        _log.debug("Install classification check failed", exc_info=True)
        return DoctorResult(
            Severity.OK, check_name, "Install classification check skipped (unexpected error)"
        )


def _check_update_dismissal_state(home: Path | None = None) -> DoctorResult:
    """Report the current update-prompt dismissal state."""
    check_name = "update_dismissal_state"
    _home = home or Path.home()
    try:
        from autoskillit.cli._install_info import detect_install, dismissal_window
        from autoskillit.cli._update_checks import _read_dismiss_state

        state = _read_dismiss_state(_home)
        entry = state.get("update_prompt")
        if not isinstance(entry, dict) or "dismissed_at" not in entry:
            return DoctorResult(Severity.OK, check_name, "No active dismissal")

        from datetime import datetime

        info = detect_install()
        window = dismissal_window(info)
        dismissed_at = datetime.fromisoformat(str(entry["dismissed_at"]))
        expiry = (dismissed_at + window).strftime("%Y-%m-%d")
        conditions = entry.get("conditions", [])
        return DoctorResult(
            Severity.OK,
            check_name,
            f"update_prompt dismissed until {expiry}; conditions={conditions}",
        )
    except Exception:
        _log.debug("Update dismissal state check failed", exc_info=True)
        return DoctorResult(
            Severity.OK, check_name, "Update dismissal state check skipped (unexpected error)"
        )


def _check_quota_cache_schema(cache_path: Path | None = None) -> DoctorResult:
    """Check the quota cache file for schema version drift."""
    check_name = "quota_cache_schema"
    path = cache_path or (Path.home() / ".claude" / "autoskillit_quota_cache.json")
    if not path.exists():
        return DoctorResult(Severity.OK, check_name, "No quota cache present.")
    try:
        raw = json.loads(path.read_text())
    except Exception as exc:
        _log.warning("quota_cache_parse_error", path=str(path), exc_info=True)
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"Quota cache at {path} could not be parsed: {type(exc).__name__}.",
        )
    observed = raw.get("schema_version") if isinstance(raw, dict) else None
    if observed == QUOTA_CACHE_SCHEMA_VERSION:
        return DoctorResult(
            Severity.OK,
            check_name,
            f"Quota cache schema v{QUOTA_CACHE_SCHEMA_VERSION} at {path}.",
        )
    return DoctorResult(
        Severity.WARNING,
        check_name,
        f"Quota cache schema drift at {path}: observed={observed!r}, "
        f"expected={QUOTA_CACHE_SCHEMA_VERSION}.",
    )


def _check_claude_process_state_breakdown() -> DoctorResult:
    """Check current D-state and CPU usage of claude processes via ps."""
    check_name = "claude_process_state"
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid,state,pcpu,comm"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return DoctorResult(
            Severity.OK,
            check_name,
            f"ps unavailable ({type(exc).__name__}); skipping claude process check",
        )

    if result.returncode != 0:
        return DoctorResult(
            Severity.OK,
            check_name,
            f"ps exited {result.returncode}; skipping claude process check",
        )

    claude_rows: list[tuple[int, str, float]] = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split(maxsplit=3)
        if len(parts) < 4:
            continue
        comm = parts[3]
        if comm != "claude":
            continue
        try:
            claude_rows.append((int(parts[0]), parts[1], float(parts[2])))
        except ValueError:
            continue

    if not claude_rows:
        return DoctorResult(Severity.OK, check_name, "No claude processes running")

    breakdown: dict[str, int] = {}
    for _, state, _ in claude_rows:
        breakdown[state] = breakdown.get(state, 0) + 1

    summary = ", ".join(f"{s}={c}" for s, c in sorted(breakdown.items()))

    d_rows = [f"pid={pid} pcpu={pcpu}" for pid, state, pcpu in claude_rows if state == "D"]
    if d_rows:
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"claude processes in D state: {', '.join(d_rows)} (breakdown: {summary})",
        )

    return DoctorResult(
        Severity.OK,
        check_name,
        f"claude process state breakdown: {summary}",
    )


def _check_plugin_cache_exists(
    cache_dir: Path | None = None,
) -> DoctorResult:
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
        Path.home() / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
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


def _check_installed_plugins_entry(
    plugins_json_path: Path | None = None,
) -> DoctorResult:
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


def _check_ambient_session_type_leaf() -> DoctorResult:
    """Detect ambient SESSION_TYPE=leaf — common env leakage from franchise subprocesses."""
    raw = os.environ.get(SESSION_TYPE_ENV_VAR, "")
    if raw.lower() == "leaf":
        return DoctorResult(
            Severity.WARNING,
            "ambient_session_type_leaf",
            "Ambient SESSION_TYPE=leaf detected. "
            "Did you intend to set SESSION_TYPE=leaf? Franchise sessions should set "
            "SESSION_TYPE=leaf only in launched subprocesses.",
        )
    return DoctorResult(
        Severity.OK,
        "ambient_session_type_leaf",
        f"SESSION_TYPE={raw!r} (not leaf)",
    )


def _check_ambient_session_type_orchestrator() -> DoctorResult:
    """Detect ambient SESSION_TYPE=orchestrator outside a launched session."""
    raw = os.environ.get(SESSION_TYPE_ENV_VAR, "")
    if raw.lower() == "orchestrator":
        return DoctorResult(
            Severity.WARNING,
            "ambient_session_type_orchestrator",
            "Ambient SESSION_TYPE=orchestrator outside of a launched session "
            "— should only be set by autoskillit CLIs.",
        )
    return DoctorResult(
        Severity.OK,
        "ambient_session_type_orchestrator",
        "No ambient orchestrator session type",
    )


def _check_ambient_session_type_franchise() -> DoctorResult:
    """Detect ambient SESSION_TYPE=franchise outside a franchise CLI session."""
    raw = os.environ.get(SESSION_TYPE_ENV_VAR, "")
    if raw.lower() == "franchise":
        return DoctorResult(
            Severity.WARNING,
            "ambient_session_type_franchise",
            "Ambient SESSION_TYPE=franchise outside of a franchise CLI session "
            "— highest-privilege env, suspicious.",
        )
    return DoctorResult(
        Severity.OK,
        "ambient_session_type_franchise",
        "No ambient franchise session type",
    )


def _check_ambient_campaign_id() -> DoctorResult:
    """Detect ambient CAMPAIGN_ID — should only be set by dispatch_food_truck."""
    campaign_id = os.environ.get("AUTOSKILLIT_CAMPAIGN_ID", "")
    if campaign_id:
        return DoctorResult(
            Severity.WARNING,
            "ambient_campaign_id",
            f"Ambient CAMPAIGN_ID={campaign_id} — should only be set by dispatch_food_truck.",
        )
    return DoctorResult(
        Severity.OK,
        "ambient_campaign_id",
        "No ambient CAMPAIGN_ID",
    )


def _check_sous_chef_bundled() -> DoctorResult:
    """Check that the sous-chef skill directory exists."""
    sous_chef_dir = pkg_root() / "skills" / "sous-chef"
    if sous_chef_dir.is_dir():
        return DoctorResult(
            Severity.OK,
            "sous_chef_bundled",
            "Sous-chef skill directory exists",
        )
    return DoctorResult(
        Severity.ERROR,
        "sous_chef_bundled",
        f"Sous-chef skill not found at {sous_chef_dir}/. Fatal prerequisite.",
    )


def _check_franchise_dispatch_guard_registered() -> DoctorResult:
    """Check that franchise dispatch guard is registered in HOOK_REGISTRY."""
    from autoskillit.hook_registry import HOOKS_DIR

    check_name = "franchise_dispatch_guard_registered"
    if "franchise_dispatch_guard.py" not in canonical_script_basenames():
        return DoctorResult(
            Severity.ERROR,
            check_name,
            "Franchise dispatch guard not registered in hooks.json. "
            "Run: autoskillit config sync-hooks",
        )
    if not (HOOKS_DIR / "franchise_dispatch_guard.py").is_file():
        return DoctorResult(
            Severity.ERROR,
            check_name,
            "Franchise dispatch guard registered but script file missing on disk. "
            "Run: autoskillit install",
        )
    return DoctorResult(
        Severity.OK,
        check_name,
        "Franchise dispatch guard registered and accessible",
    )


def _check_stale_franchise_state(project_dir: Path | None = None) -> DoctorResult:
    """Check for stale campaign state files with running dispatches > 7 days old."""
    import time

    root = project_dir or Path.cwd()
    franchise_dir = root / ".autoskillit" / "temp" / "franchise"
    check_name = "stale_franchise_state"
    if not franchise_dir.is_dir():
        return DoctorResult(Severity.OK, check_name, "No franchise state directory")
    threshold = time.time() - (_STALE_THRESHOLD_DAYS * 86400)
    stale_paths: list[str] = []
    for campaign_dir in franchise_dir.iterdir():
        if not campaign_dir.is_dir():
            continue
        state_file = campaign_dir / "state.json"
        if not state_file.is_file():
            continue
        if state_file.stat().st_mtime > threshold:
            continue
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            dispatches = data.get("dispatches", [])
            has_running = any(d.get("status") == "running" for d in dispatches)
            if has_running:
                stale_paths.append(str(state_file))
        except (json.JSONDecodeError, OSError, KeyError):
            continue
    if stale_paths:
        paths_str = ", ".join(stale_paths)
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"Stale campaign state > {_STALE_THRESHOLD_DAYS} days: {paths_str}. "
            f"Run: autoskillit franchise status <id> --reap",
        )
    return DoctorResult(Severity.OK, check_name, "No stale franchise state files")


def _check_campaign_onboarding_hint(project_dir: Path | None = None) -> DoctorResult:
    """Hint when no campaign recipes exist yet."""
    root = project_dir or Path.cwd()
    campaigns_dir = root / ".autoskillit" / "recipes" / "campaigns"
    check_name = "campaign_onboarding_hint"
    if not campaigns_dir.is_dir():
        return DoctorResult(
            Severity.INFO,
            check_name,
            "No campaign recipes found. Get started: /autoskillit:make-campaign <description>",
        )
    yaml_files = [
        f for f in campaigns_dir.iterdir() if f.suffix in (".yaml", ".yml") and f.is_file()
    ]
    if not yaml_files:
        return DoctorResult(
            Severity.INFO,
            check_name,
            "No campaign recipes found. Get started: /autoskillit:make-campaign <description>",
        )
    return DoctorResult(
        Severity.OK,
        check_name,
        f"{len(yaml_files)} campaign recipe(s) found",
    )


def _check_campaign_manifest_clone_dests(project_dir: Path | None = None) -> DoctorResult:
    """Check that dispatches within campaign recipes use unique clone destinations."""
    from autoskillit.core import YAMLError, load_yaml

    root = project_dir or Path.cwd()
    campaigns_dir = root / ".autoskillit" / "recipes" / "campaigns"
    check_name = "campaign_manifest_clone_dests"
    if not campaigns_dir.is_dir():
        return DoctorResult(Severity.OK, check_name, "No campaigns directory")
    yaml_files = [
        f for f in campaigns_dir.iterdir() if f.suffix in (".yaml", ".yml") and f.is_file()
    ]
    if not yaml_files:
        return DoctorResult(Severity.OK, check_name, "No campaign recipes to check")
    seen_paths: dict[str, list[str]] = {}
    for yaml_file in yaml_files:
        try:
            data = load_yaml(yaml_file)
        except (YAMLError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        dispatches = data.get("dispatches", [])
        if not isinstance(dispatches, list):
            continue
        recipe_name = data.get("name", yaml_file.stem)
        for dispatch in dispatches:
            if not isinstance(dispatch, dict):
                continue
            ingredients = dispatch.get("ingredients", {})
            if not isinstance(ingredients, dict):
                continue
            clone_path = ingredients.get("clone_path", "")
            if clone_path:
                key = str(clone_path)
                label = f"{recipe_name}:{dispatch.get('name', '?')}"
                seen_paths.setdefault(key, []).append(label)
    duplicates = {path: users for path, users in seen_paths.items() if len(users) > 1}
    if duplicates:
        dup_details = "; ".join(
            f"{path} (used by {', '.join(users)})" for path, users in duplicates.items()
        )
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"Dispatches share a literal clone destination: {dup_details}. "
            f"Use unique clone paths per dispatch.",
        )
    return DoctorResult(Severity.OK, check_name, "All dispatch clone destinations unique")


def _check_feature_dependencies(features: dict[str, bool]) -> DoctorResult:
    """Verify all enabled features have their dependencies satisfied."""
    from autoskillit.core import FEATURE_REGISTRY

    violations: list[str] = []
    for name, defn in FEATURE_REGISTRY.items():
        if not features.get(name, defn.default_enabled) or not defn.depends_on:
            continue
        for dep in defn.depends_on:
            dep_defn = FEATURE_REGISTRY.get(dep)
            dep_default = dep_defn.default_enabled if dep_defn is not None else False
            if not features.get(dep, dep_default):
                violations.append(
                    f"Feature '{name}' requires '{dep}' but '{dep}' is disabled. "
                    f"Enable '{dep}' in features config first."
                )
    if violations:
        return DoctorResult(
            severity=Severity.ERROR,
            check="feature_dependencies",
            message="; ".join(violations),
        )
    return DoctorResult(
        severity=Severity.OK,
        check="feature_dependencies",
        message="All feature dependencies satisfied",
    )


def _check_feature_registry_consistency() -> DoctorResult:
    """Verify FEATURE_REGISTRY import_package entries resolve to importable modules."""
    import importlib

    from autoskillit.core import FEATURE_REGISTRY

    failures: list[str] = []
    for name, defn in FEATURE_REGISTRY.items():
        if not defn.import_package:
            continue
        try:
            importlib.import_module(defn.import_package)
        except ImportError as exc:
            failures.append(
                f"Feature '{name}' import_package {defn.import_package!r} "
                f"cannot be imported: {exc}"
            )
    if failures:
        return DoctorResult(
            severity=Severity.ERROR,
            check="feature_registry_consistency",
            message="; ".join(failures),
        )
    return DoctorResult(
        severity=Severity.OK,
        check="feature_registry_consistency",
        message="All FEATURE_REGISTRY import packages resolve",
    )


def run_doctor(*, output_json: bool = False) -> None:
    """Check project setup for common issues."""
    cfg = load_config(Path.cwd())
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

    # Check 2b: Dual MCP registration — direct entry and marketplace plugin both present
    results.append(_check_dual_mcp_registration())

    # Check 2c: Plugin cache directory exists
    results.append(_check_plugin_cache_exists())

    # Check 2d: installed_plugins.json has autoskillit entry
    results.append(_check_installed_plugins_entry())

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

    # Check 6: Hook executability — validates deployed scripts for all event types (all scopes)
    results.extend(_check_hook_health_all_scopes(Path.cwd()))

    # Check 7: Hook registration in settings.json
    results.append(_check_hook_registration(_claude_settings_path("user")))

    # Check 7b: Hook registry drift (multi-scope)
    from autoskillit.hook_registry import iter_all_scope_paths

    for scope_label, settings_path in iter_all_scope_paths(Path.cwd()):
        results.append(_check_hook_registry_drift(settings_path, scope_label=scope_label))

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

    # -- Franchise doctor checks (ambient env + infrastructure health) --

    # Check 18: Ambient SESSION_TYPE=leaf leak detection
    results.append(_check_ambient_session_type_leaf())

    # Check 19: Ambient SESSION_TYPE=orchestrator leak detection
    results.append(_check_ambient_session_type_orchestrator())

    # Check 20: Ambient SESSION_TYPE=franchise leak detection
    results.append(_check_ambient_session_type_franchise())

    # Check 21: Ambient CAMPAIGN_ID leak detection
    results.append(_check_ambient_campaign_id())

    # Check 22: Feature dependency consistency
    results.append(_check_feature_dependencies(cfg.features))

    # Check 23: Feature registry import consistency
    results.append(_check_feature_registry_consistency())

    # Checks 24–28: Franchise infrastructure — only when franchise feature is enabled
    if is_feature_enabled("franchise", cfg.features):
        # Check 24: Sous-chef skill directory exists
        results.append(_check_sous_chef_bundled())

        # Check 25: Franchise dispatch guard registered
        results.append(_check_franchise_dispatch_guard_registered())

        # Check 26: Stale franchise state (running > 7 days)
        results.append(_check_stale_franchise_state())

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
