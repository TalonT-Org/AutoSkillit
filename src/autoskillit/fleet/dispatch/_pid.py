"""``_write_pid`` on_spawn helper — moved from fleet/_api.py (#4851).

L2 — Fail-closed: if ``mark_dispatch_running`` raises (e.g. illegal state
transition), the spawned child is killed via ``kill_process_tree`` (the
canonical sync kill primitive used by ``_dispatch_reaper``) and the
exception's message string is returned to the caller via closure-scoped
state. Raising the exception from ``_on_spawn`` is NOT safe because
``_execute_claude_headless`` catches runner exceptions and returns
``SkillResult.crashed`` — the propagated exception would never reach the
outer ``execute_dispatch`` wrapper. The caller therefore inspects the
returned error string (or the closure-scoped ``_spawn_error`` list) and
translates it into a ``FLEET_L3_STARTUP_OR_CRASH`` envelope.
"""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import get_logger
from autoskillit.fleet.state_types import DispatchEffectName, DispatchProvenanceTracker

logger = get_logger(__name__)


def _write_pid(
    state_path: Path,
    dispatch_name: str,
    dispatch_id: str,
    pid: int,
    starttime_ticks: int,
    sidecar_path: str | None = None,
    dispatched_create_time: float = 0.0,
    identity_degraded: bool = False,
    issue_url: str = "",
    dispatched_boot_id: str = "",
    provenance: DispatchProvenanceTracker | None = None,
    *,
    enforce_max_resume_attempts: bool = False,
) -> str | None:
    """on_spawn callback: atomically mark dispatch as running with dispatched_pid.

    Fail-closed: if ``mark_dispatch_running`` raises (e.g. illegal state
    transition), the spawned child is killed via ``kill_process_tree`` (the
    canonical sync kill primitive used by ``_dispatch_reaper``) and the
    exception's message string is returned to the caller via closure-scoped
    state. Raising the exception from ``_on_spawn`` is NOT safe because
    ``_execute_claude_headless`` catches runner exceptions and returns
    ``SkillResult.crashed`` — the propagated exception would never reach the
    outer ``execute_dispatch`` wrapper. The caller therefore inspects the
    returned error string (or the closure-scoped ``_spawn_error`` list) and
    translates it into a ``FLEET_L3_STARTUP_OR_CRASH`` envelope.

    Returns:
        None on success; the formatted error message string on failure (also
        recorded via the side-effect of having killed the child).
    """
    from autoskillit.execution import kill_process_tree  # noqa: PLC0415
    from autoskillit.fleet import mark_dispatch_running

    try:
        mark_dispatch_running(
            state_path,
            dispatch_name,
            dispatch_id=dispatch_id,
            dispatched_pid=pid,
            starttime_ticks=starttime_ticks,
            boot_id=dispatched_boot_id,
            dispatched_create_time=dispatched_create_time,
            sidecar_path=sidecar_path,
            identity_degraded=identity_degraded,
            issue_url=issue_url,
            enforce_max_resume_attempts=enforce_max_resume_attempts,
        )
        return None
    except Exception as exc:
        # Fail-closed: kill the child before the state record can diverge.
        if pid:
            try:
                if provenance is not None:
                    provenance.start(
                        DispatchEffectName.LOCAL_PROCESS_CLEANUP,
                        identities={"pid": pid},
                    )
                cleanup_result = kill_process_tree(pid, timeout=2.0)
                if provenance is not None:
                    provenance.record_local_cleanup(cleanup_result)
                    if cleanup_result.complete:
                        provenance.confirm(
                            DispatchEffectName.LOCAL_PROCESS_CLEANUP,
                            receipt="bounded process-tree observation confirmed complete cleanup",
                            identities={"pid": pid},
                        )
                    else:
                        provenance.mark_ambiguous(
                            DispatchEffectName.LOCAL_PROCESS_CLEANUP,
                            evidence="local process-tree cleanup observation was incomplete",
                            identities={"pid": pid},
                        )
            except Exception:
                logger.warning(
                    "_write_pid: kill_process_tree failed for pid=%d",
                    pid,
                    exc_info=True,
                )
        cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
        cause_str = f" caused by {type(cause).__name__}: {cause}" if cause is not None else ""
        return f"_on_spawn transition failed: {type(exc).__name__}: {exc}{cause_str}"
