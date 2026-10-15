"""Durable outcome accounting for the workspace commit tool."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from autoskillit.core import (
    CommitFailureClass,
    WorkspaceOutcomeKind,
    WorkspaceOutcomeRecord,
    get_logger,
)

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext

__all__ = ["_finish_commit_response"]

logger = get_logger(__name__)


def _finish_commit_response(
    response: dict[str, object],
    *,
    failure_class: CommitFailureClass | None,
    tool_ctx: ToolContext,
    workspace: str,
) -> str:
    """Persist the authoritative commit outcome and render its response."""
    envelope = dict(response)
    if failure_class is not None:
        envelope["failure_class"] = failure_class.value
    commit_sha = envelope.get("commit_sha")
    succeeded = envelope.get("success") is True
    try:
        tool_ctx.workspace_outcome_ledger.record(
            WorkspaceOutcomeRecord(
                workspace=workspace,
                recorded_at=datetime.now(UTC).isoformat(),
                kind=WorkspaceOutcomeKind.COMMIT_ATTEMPT,
                succeeded=succeeded,
                commit_sha=commit_sha if isinstance(commit_sha, str) else None,
                failure_class=None if succeeded else failure_class,
            )
        )
    except Exception as exc:
        logger.error("commit_files outcome recording failed", exc_info=True)
        ledger_failure: dict[str, object] = {
            "success": False,
            "error": f"workspace outcome recording failed: {type(exc).__name__}: {exc}",
            "failure_class": CommitFailureClass.UNHANDLED.value,
        }
        if isinstance(commit_sha, str) and commit_sha:
            ledger_failure["commit_sha"] = commit_sha
        return json.dumps(ledger_failure)
    return json.dumps(envelope)
