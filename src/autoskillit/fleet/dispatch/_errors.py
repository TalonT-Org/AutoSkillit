"""Shared error helpers for the fleet dispatch engine (#4851).

`complete_failure_with_state` was a closure inside the original `fleet/_api.py`
(referenced from Phase B and Phase C). Decomposing the function into a free
helper lets the per-phase shards (`_lineage.py`, `_execution.py`) hand back a
`DispatchResult` without re-importing the closure-scoped variables.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    FleetErrorCode,
    ManagedHeadlessSessionLineageRef,
    ManagedHeadlessSessionTerminalState,
    get_logger,
)
from autoskillit.fleet._native_shell_capture import set_lineage_terminal_state
from autoskillit.fleet.state import append_dispatch_record
from autoskillit.fleet.state_types import (
    DispatchCompleted,
    DispatchProvenanceTracker,
    DispatchRecord,
    DispatchResult,
    DispatchStatus,
)

if TYPE_CHECKING:
    from autoskillit.pipeline.context import ToolContext

_logger = get_logger(__name__)


def complete_failure_with_state(
    *,
    error_code: FleetErrorCode,
    message: str,
    dispatch_status: DispatchStatus = DispatchStatus.REFUSED,
    dispatched_session_id: str = "",
    dispatch_id: str | None,
    managed_lineage_ref: ManagedHeadlessSessionLineageRef | None,
    provenance: DispatchProvenanceTracker,
    state_path: Path | None,
    effective_name: str | None,
    tool_ctx: ToolContext,
) -> DispatchResult:
    """Post-dispatch-id failure path — writes per-dispatch state only.

    Mirrors the legacy `_complete_failure_with_state` closure from
    `fleet/_api.py:713`. The closure-scoped variables (`dispatch_id`,
    `managed_lineage_ref`, `provenance`, `state_path`, `effective_name`,
    `tool_ctx`) become explicit keyword arguments.

    When `state_path` is `None`, no per-dispatch state write is attempted and
    the returned `DispatchResult.per_dispatch_state_path` is `None`.
    """
    completed = DispatchCompleted(
        success=False,
        dispatch_status=dispatch_status,
        dispatch_id=dispatch_id or "",
        dispatched_session_id=dispatched_session_id,
        reason=error_code,
        diagnostic_message=message,
        effect_provenance=provenance.snapshot(),
    )
    if managed_lineage_ref is not None:
        try:
            set_lineage_terminal_state(
                tool_ctx,
                managed_lineage_ref,
                ManagedHeadlessSessionTerminalState.FAILED,
            )
        except Exception:
            _logger.warning(
                "_complete_failure_with_state: managed lineage close failed",
                exc_info=True,
            )
    if state_path is None or effective_name is None:
        return DispatchResult(completed, per_dispatch_state_path=None)
    try:
        append_dispatch_record(
            state_path,
            DispatchRecord(
                name=effective_name,
                status=dispatch_status,
                reason=str(error_code),
                diagnostic_message=message,
                dispatch_id=dispatch_id or "",
                dispatched_session_id=dispatched_session_id,
                effect_provenance=provenance.snapshot().to_dict(),
                managed_lineage_ref=managed_lineage_ref,
            ),
        )
    except Exception:
        _logger.warning(
            "_complete_failure_with_state: per-dispatch state write failed",
            exc_info=True,
        )
        return DispatchResult(completed, per_dispatch_state_path=None)
    return DispatchResult(completed, per_dispatch_state_path=state_path)
