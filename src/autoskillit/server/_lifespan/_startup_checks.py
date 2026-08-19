"""One-shot synchronous startup checks for the FastMCP server lifespan.

Each function here runs at MCP server startup (either inline in
``_autoskillit_lifespan`` or offloaded via ``_lifespan._run_*_async``).
Failures are logged and swallowed because the server must stay up even when
diagnostics find problems.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import autoskillit.core.paths as _core_paths
from autoskillit.core import (
    atomic_write,
    get_logger,
    installed_plugin_cache_dir,
)
from autoskillit.execution import (
    BACKEND_REGISTRY,
    RecordingSubprocessRunner,
    find_broken_codex_hook_commands,
)
from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    find_broken_hook_scripts,
    render_hooks_json_text,
)

# Late-binding for monkeypatch reach: tests patch
# "autoskillit.server._lifespan.iter_all_scope_paths",
# "autoskillit.server._lifespan.validate_plugin_cache_hooks",
# "autoskillit.server._lifespan.repair_broken_plugin_cache_hooks", and
# "autoskillit.server._lifespan._get_ctx_or_none" (the package facade), so
# these must be resolved via attribute access on the package at call time
# rather than imported by name into this submodule.
from autoskillit.server import _lifespan as _lifespan_pkg
from autoskillit.workspace import (
    PluginHookRepairStatus,
    read_obligation,
    repair_broken_projection_hooks,
    verify_install_state,
)

logger = get_logger(__name__)


def _retain_context_tracker_authority(ctx: Any) -> None:
    from autoskillit.server.tools.tools_kitchen._tracker_authority import (  # circular-break
        _retain_kitchen_tracker_authority,
    )

    _retain_kitchen_tracker_authority(ctx)


def run_startup_drift_check() -> None:
    """Compare on-disk hooks.json bytes vs current render; regenerate if stale.

    Any byte difference triggers a rewrite. Render and file-I/O failures are
    logged and contained because the check runs as a lifespan background task.
    """
    hooks_json_path = _core_paths.pkg_root() / "hooks" / "hooks.json"
    try:
        expected = render_hooks_json_text()
    except Exception:
        logger.exception("startup_drift_check_render_failed")
        return
    try:
        try:
            on_disk = hooks_json_path.read_text(encoding="utf-8")
        except OSError:
            on_disk = None
        if on_disk != expected:
            logger.info(
                "startup_drift_detected",
                reason="content_mismatch",
            )
            atomic_write(hooks_json_path, expected)
            logger.info("hooks_json_self_healed", path=str(hooks_json_path))
        else:
            logger.info("startup_drift_check_ok")
    except Exception:
        logger.exception("startup_drift_check_failed")


def _activate_recipe_kitchen(kitchen_id: str) -> None:
    """Publish one kitchen to the recipe-generation lifecycle."""
    from autoskillit.server._recipe_generation import activate_kitchen  # circular-break

    activate_kitchen(kitchen_id)


def run_startup_hook_health_check() -> list[str]:
    """Detect broken hook scripts across all settings scopes on MCP startup.

    Called as a background task alongside run_startup_drift_check().
    Returns list of broken hook commands. Any failure is logged and swallowed.

    On broken plugin-cache hooks OR a pending publication obligation, also
    attempts an in-process repair of the plugin cache's hook artifacts (the
    server must not shell out, per its existing design — see
    workspace._projected_artifact._hook_repair). This reduces the broken
    window while the obligation remains until a full verified install
    clears it; the in-process repair alone cannot perform that full
    publication, so it never clears the obligation itself.
    """
    broken: list[str] = []
    try:
        for scope_label, settings_path in _lifespan_pkg.iter_all_scope_paths(None):
            scope_broken = find_broken_hook_scripts(settings_path)
            if scope_broken:
                broken.extend(scope_broken)
                logger.warning(
                    "stale_hook_paths_detected",
                    scope=scope_label,
                    broken=scope_broken,
                )
        pending_obligation = read_obligation(Path.home())
    except Exception:
        logger.exception("startup_hook_health_check_failed")
        return []

    try:
        cache_broken = _lifespan_pkg.validate_plugin_cache_hooks()
        if cache_broken:
            broken.extend(cache_broken)
            logger.warning(
                "stale_plugin_cache_hooks_detected",
                broken=cache_broken,
                remediation="Run `autoskillit install` from an external terminal",
            )
    except Exception:
        logger.exception("startup_plugin_cache_hook_validation_failed")
        cache_broken = ["plugin cache hook validation failed"]

    if cache_broken or pending_obligation is not None:
        cache_dir = installed_plugin_cache_dir(Path.home(), "autoskillit")
        try:
            for outcome in _lifespan_pkg.repair_broken_plugin_cache_hooks(cache_dir):
                if outcome.status is PluginHookRepairStatus.REPAIRED:
                    logger.info(
                        "plugin_cache_hooks_repaired_at_startup",
                        incarnation=str(outcome.incarnation_dir),
                    )
                elif outcome.status is PluginHookRepairStatus.CONTENDED:
                    logger.warning(
                        "plugin_cache_hooks_repair_contended_at_startup",
                        incarnation=str(outcome.incarnation_dir),
                        reason=outcome.detail,
                    )
                else:
                    logger.error(
                        "plugin_cache_hooks_repair_failed_at_startup",
                        incarnation=str(outcome.incarnation_dir),
                        reason=outcome.detail,
                    )
        except Exception:
            logger.exception("startup_hook_repair_failed")

    # Projection repair — independent failure domain.  Must run even when the
    # plugin cache is healthy and no obligation is pending (projection-only
    # staleness).  NOT inside the cache_broken/pending_obligation gate above.
    try:
        for outcome in repair_broken_projection_hooks():
            if outcome.status is PluginHookRepairStatus.REPAIRED:
                logger.info(
                    "projection_hooks_repaired_at_startup",
                    incarnation=str(outcome.incarnation_dir),
                )
            elif outcome.status is PluginHookRepairStatus.CONTENDED:
                logger.warning(
                    "projection_hooks_repair_contended_at_startup",
                    incarnation=str(outcome.incarnation_dir),
                    reason=outcome.detail,
                )
            else:
                logger.error(
                    "projection_hooks_repair_failed_at_startup",
                    incarnation=str(outcome.incarnation_dir),
                    reason=outcome.detail,
                )
    except Exception:
        logger.exception("startup_projection_hook_repair_failed")

    # Codex config hook detection — detection-only (repair happens at sync time).
    try:
        codex_broken = find_broken_codex_hook_commands()
        if codex_broken:
            broken.extend(codex_broken)
            logger.warning(
                "stale_codex_hook_commands_detected",
                broken=codex_broken,
                remediation="Run `autoskillit install` or re-sync Codex hooks",
            )
    except Exception:
        logger.exception("startup_codex_hook_detection_failed")

    return broken


def run_startup_install_state_check() -> list[str]:
    """Report install-state inconsistencies on MCP startup.

    The third consumer of ``verify_install_state()`` (alongside ``doctor`` and
    post-install verification), so the authority cannot decay into a function
    nobody calls. Diagnostic only: startup never fails on a finding, because
    the projection no longer depends on any of the artifacts being checked.
    Any failure is logged and swallowed.
    """
    try:
        findings = verify_install_state()
        for finding in findings:
            logger.warning(
                "install_state_inconsistent",
                check=finding.check,
                message=finding.message,
                remediation="Run `autoskillit install` from an external terminal",
            )
        return [f.check for f in findings]
    except Exception:
        logger.exception("startup_install_state_check_failed")
        return []


def run_startup_fix_required_coverage_check() -> None:
    """Validate that fix-required hook script stems are covered by at least one backend.

    The dispatch gate in tools_execution._check_backend_compat refuses all skill
    dispatches on a backend if HOOK_REGISTRY contains fix-required hooks whose
    script stems are not in that backend's applicable_guards. This check provides
    defense-in-depth: if the cross-registry invariant is violated, the server
    fails to start rather than accepting requests it will later crash on.

    Raises RuntimeError if any fix-required hook's script stems are not covered
    by the union of all registered backends' applicable_guards. A fix-required
    hook that IS covered by at least one backend is valid and does not raise.
    """
    all_guards: set[str] = set()
    for cls in BACKEND_REGISTRY.values():
        try:
            all_guards.update(cls().capabilities.applicable_guards)
        except Exception as exc:
            raise RuntimeError(
                f"Backend {cls.__name__!r} constructor raised during startup "
                f"fix-required coverage check: {exc}"
            ) from exc
    for h in HOOK_REGISTRY:
        if h.codex_status != "fix-required":
            continue
        stems = frozenset(Path(s).stem for s in h.scripts) if h.scripts else frozenset()
        if stems and not stems.issubset(all_guards):
            missing = sorted(stems - all_guards)
            raise RuntimeError(
                f"HOOK_REGISTRY fix-required entry (matcher={h.matcher!r}) has "
                f"guard scripts {missing} not covered by any backend's "
                f"applicable_guards. This will brick dispatch for backends "
                f"missing these guards."
            )


def _finalize_recorder() -> None:
    """Finalize the recording subprocess runner if one is active."""
    ctx = _lifespan_pkg._get_ctx_or_none()
    if ctx is not None and isinstance(ctx.runner, RecordingSubprocessRunner):
        try:
            ctx.runner.recorder.finalize()
        except Exception:
            logger.exception("recorder.finalize() failed during lifespan teardown")
