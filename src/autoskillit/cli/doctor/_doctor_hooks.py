"""Hook registration and health doctor checks."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import Severity, get_logger
from autoskillit.hook_registry import (
    _count_hook_registry_drift,
    canonical_script_basenames,
    find_broken_hook_scripts,
)

from ._doctor_types import DoctorResult

logger = get_logger(__name__)


def _check_hook_registration(settings_path: Path) -> DoctorResult:
    from autoskillit.cli._init_helpers import _is_plugin_installed

    if _is_plugin_installed():
        return DoctorResult(
            severity=Severity.OK,
            check="hook_registration",
            message="Hooks delivered via plugin cache — settings.json check skipped.",
        )

    from autoskillit.cli._hooks import _load_settings_data

    data = _load_settings_data(settings_path)
    commands = [
        hook.get("command", "")
        for event_entries in data.get("hooks", {}).values()
        if isinstance(event_entries, list)
        for entry in event_entries
        for hook in entry.get("hooks", [])
    ]
    registered_logical = {cmd.split()[-1] for cmd in commands if "_dispatch.py" in cmd}
    missing = [
        script
        for script in canonical_script_basenames()
        if not any(script in cmd for cmd in commands)
        and script.removesuffix(".py") not in registered_logical
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
        from autoskillit.cli._init_helpers import _is_plugin_installed

        if _is_plugin_installed():
            msg = "Hooks delivered via plugin cache — settings.json drift is expected."
            if scope_label:
                msg = f"[{scope_label}] {msg}"
            return DoctorResult(
                severity=Severity.OK,
                check="hook_registry_drift",
                message=msg,
            )
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
    """Verify all deployed hook scripts in a single settings file exist on disk."""
    broken = find_broken_hook_scripts(settings_path)
    if broken:
        return DoctorResult(
            severity=Severity.ERROR,
            check="hook_health",
            message=f"Hook scripts not found: {', '.join(broken)}",
        )
    return DoctorResult(Severity.OK, "hook_health", "All hook scripts accessible")


def _check_hook_registry_drift_all_scopes(project_root: Path | None = None) -> list[DoctorResult]:
    """Check hook registry drift across ALL scopes."""
    from autoskillit.hook_registry import iter_all_scope_paths

    results: list[DoctorResult] = []
    for scope_label, settings_path in iter_all_scope_paths(project_root):
        results.append(_check_hook_registry_drift(settings_path, scope_label=scope_label))
    return results


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


def _check_dual_registration(settings_path: Path) -> DoctorResult:
    """Detect dual hook registration (plugin active + hooks in settings.json)."""
    from autoskillit.cli._hooks import _load_settings_data
    from autoskillit.cli._init_helpers import _is_plugin_installed

    if not _is_plugin_installed():
        return DoctorResult(
            severity=Severity.OK,
            check="dual_registration",
            message="Plugin not active — no dual registration possible.",
        )

    data = _load_settings_data(settings_path)
    all_commands = [
        hook.get("command", "")
        for event_entries in data.get("hooks", {}).values()
        if isinstance(event_entries, list)
        for entry in event_entries
        for hook in entry.get("hooks", [])
    ]
    autoskillit_hooks = [c for c in all_commands if "_dispatch.py" in c]
    if autoskillit_hooks:
        return DoctorResult(
            severity=Severity.WARNING,
            check="dual_registration",
            message=(
                f"Plugin is active but {len(autoskillit_hooks)} autoskillit hook(s) "
                f"are also present in settings.json. Hooks will fire twice. "
                f"Run 'autoskillit install' to fix."
            ),
        )
    return DoctorResult(
        severity=Severity.OK,
        check="dual_registration",
        message="Plugin active — no duplicate hooks in settings.json.",
    )
