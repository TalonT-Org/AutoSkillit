"""Phase D: cancellation / exception / finally cleanup — moved from fleet/_api.py (#4851).

Holds the three handlers that wrap the inner ``run_execution`` body plus the
shared ``_post_dispatch_cleanup`` helper called from ``finalize_state_write``.
Each handler preserves the shielded/unshielded split from the original
``_run_dispatch``:

* ``handle_cancellation`` — shields lineage close + process kill + state write.
* ``handle_generic_exception`` — UNSHIELDED lineage close to FAILED (the
  original does not shield this branch).
* ``run_finally_label_cleanup`` — shields the LABEL_CLEANUP provenance cycle.

``_post_dispatch_cleanup`` runs quota cache invalidation and the background
quota refresh; the orchestrator calls it from the finalize shard.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import anyio

from autoskillit.core import (
    ManagedHeadlessSessionLineageRef,
    ManagedHeadlessSessionTerminalState,
    get_logger,
)
from autoskillit.fleet._native_shell_capture import set_lineage_terminal_state
from autoskillit.fleet.dispatch._execution import SpawnContext
from autoskillit.fleet.state_types import DispatchEffectName, DispatchProvenanceTracker

if TYPE_CHECKING:
    from autoskillit.core import SkillResult
    from autoskillit.pipeline.context import ToolContext

logger = get_logger(__name__)


async def handle_cancellation(
    *,
    spawn_ctx: SpawnContext,
    tool_ctx: ToolContext,
    effective_name: str,
    managed_lineage_ref: ManagedHeadlessSessionLineageRef | None,
    provenance: DispatchProvenanceTracker,
    marker_dir: Path | None,
    state_path: Path,
) -> NoReturn:
    """Phase D handler for asyncio.CancelledError.

    Fires the shielded lineage close → process tree kill → state interrupted
    write sequence, then re-raises. The orchestrator's outer except clause
    catches and re-raises the cancelled error to propagate the cancellation.
    """
    provenance.request_cancel()
    try:
        with anyio.CancelScope(shield=True):
            set_lineage_terminal_state(
                tool_ctx,
                managed_lineage_ref,  # type: ignore[arg-type]
                ManagedHeadlessSessionTerminalState.CANCELLED,
            )
    except Exception:
        logger.warning(
            "failed to record managed lineage cancellation",
            dispatch_name=effective_name,
            exc_info=True,
        )
    if spawn_ctx.dispatched_pid:
        try:
            from autoskillit.execution import kill_process_tree  # noqa: PLC0415

            provenance.start(
                DispatchEffectName.LOCAL_PROCESS_CLEANUP,
                identities={"pid": spawn_ctx.dispatched_pid[0]},
            )
            with anyio.CancelScope(shield=True):
                cleanup_result = await anyio.to_thread.run_sync(
                    kill_process_tree,
                    spawn_ctx.dispatched_pid[0],
                    2.0,
                )
            provenance.record_local_cleanup(cleanup_result)
            if cleanup_result.complete:
                provenance.confirm(
                    DispatchEffectName.LOCAL_PROCESS_CLEANUP,
                    receipt="bounded process-tree observation confirmed complete cleanup",
                    identities={"pid": spawn_ctx.dispatched_pid[0]},
                )
            else:
                provenance.mark_ambiguous(
                    DispatchEffectName.LOCAL_PROCESS_CLEANUP,
                    evidence="local process-tree cleanup observation was incomplete",
                    identities={"pid": spawn_ctx.dispatched_pid[0]},
                )
        except Exception:
            provenance.mark_ambiguous(
                DispatchEffectName.LOCAL_PROCESS_CLEANUP,
                evidence="local process-tree cleanup raised",
                identities={"pid": spawn_ctx.dispatched_pid[0]},
            )
            logger.warning(
                "failed to capture local process cleanup evidence",
                dispatch_name=effective_name,
                exc_info=True,
            )
        try:
            from autoskillit.fleet.state import mark_dispatch_interrupted  # noqa: PLC0415

            captured_session_id = (
                spawn_ctx.dispatched_session_id[0] if spawn_ctx.dispatched_session_id else ""
            )

            with anyio.CancelScope(shield=True):
                provenance.record_state_cleanup(confirmed=True)
                mark_dispatch_interrupted(
                    state_path,
                    effective_name,
                    reason="signal_induced_cancellation",
                    dispatched_session_id=captured_session_id,
                    dispatched_session_log_dir=str(marker_dir) if marker_dir is not None else "",
                    effect_provenance=provenance.snapshot().to_dict(),
                )
        except Exception:
            provenance.record_state_cleanup(confirmed=False)
            logger.warning(
                "failed to record interrupted state on cancel",
                dispatch_name=effective_name,
                exc_info=True,
            )
    raise


async def handle_generic_exception(
    *,
    tool_ctx: ToolContext,
    managed_lineage_ref: ManagedHeadlessSessionLineageRef | None,
) -> NoReturn:
    """Generic ``except Exception`` handler — UNSHIELDED lineage close to FAILED.

    Matches the original where this branch never shielded the lineage state
    mutation. Re-raises the active exception.
    """
    try:
        set_lineage_terminal_state(
            tool_ctx,
            managed_lineage_ref,  # type: ignore[arg-type]
            ManagedHeadlessSessionTerminalState.FAILED,
        )
    except Exception:
        logger.warning(
            "failed to record managed lineage failure",
            exc_info=True,
        )
    raise


async def run_finally_label_cleanup(
    *,
    spawn_ctx: SpawnContext,
    dispatch_id: str,
    dispatch_sidecar_path: str,
    tool_ctx: ToolContext,
    provenance: DispatchProvenanceTracker,
) -> bool:
    """Finally-block label cleanup — SHIELDED from cancellation.

    Returns ``labels_cleaned: bool``. The orchestrator calls this only when
    the dispatch did NOT complete normally, mirroring the original guard on
    ``if not _dispatch_completed_normally:``.
    """
    from autoskillit.fleet._label_cleanup import cleanup_orphaned_labels  # noqa: PLC0415

    with anyio.CancelScope(shield=True):
        provenance.start(
            DispatchEffectName.LABEL_CLEANUP,
            identities={"dispatch_id": dispatch_id},
        )
        labels_cleaned = await cleanup_orphaned_labels(
            dispatch_sidecar_path,
            tool_ctx.github_client,
            issue_url=spawn_ctx.issue_urls_raw,
        )
        provenance.record_labels_cleanup(confirmed=labels_cleaned)
        if labels_cleaned:
            provenance.confirm(
                DispatchEffectName.LABEL_CLEANUP,
                receipt="cancellation cleanup helper confirmed label cleanup",
                identities={"dispatch_id": dispatch_id},
            )
        else:
            provenance.mark_ambiguous(
                DispatchEffectName.LABEL_CLEANUP,
                evidence="cancellation cleanup did not confirm label cleanup",
                identities={"dispatch_id": dispatch_id},
            )
    return labels_cleaned


def _post_dispatch_cleanup(
    tool_ctx: ToolContext,
    skill_result: SkillResult,
    cache_invalidator: Callable[[str], None] | None,
    quota_refresher: Callable[..., Any],
) -> None:
    """Line 231-245: quota cache invalidation + background quota refresh."""
    if cache_invalidator is not None:
        cache_invalidator(tool_ctx.config.quota_guard.cache_path)

    if tool_ctx.background is not None:
        tool_ctx.background.submit(
            quota_refresher(tool_ctx.config.quota_guard),
            label="quota_post_dispatch_refresh",
        )
