"""Terminal OTLP model-evidence resolution for headless execution."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import TYPE_CHECKING

import anyio

from autoskillit.core import ModelIdentity, SubagentModelOutcomeDict, get_logger

if TYPE_CHECKING:
    from autoskillit.execution.otlp_sink import LocalOtlpSink

logger = get_logger(__name__)


def _capture_native_session_ids(
    downstream: Callable[[str], None] | None,
) -> tuple[list[str], Callable[[str], None]]:
    captured = [""]

    def capture(candidate: str) -> None:
        if candidate:
            captured[0] = candidate
        if downstream is not None:
            downstream(candidate)

    return captured, capture


def _drain_model_evidence(
    sink: LocalOtlpSink,
    *,
    terminal_session_id: str,
    captured_session_id: str,
    model_identity: ModelIdentity,
) -> tuple[str, ModelIdentity, tuple[SubagentModelOutcomeDict, ...]]:
    try:
        with anyio.CancelScope(shield=True):
            sink.close()
    except Exception:
        logger.debug("local_otlp_sink_close_failed", exc_info=True)
    evidence_session_id = terminal_session_id or captured_session_id
    try:
        resolved_parent_model, outcomes = sink.model_evidence_for(evidence_session_id)
    except Exception:
        logger.warning("local_otlp_sink_model_evidence_failed", exc_info=True)
        resolved_parent_model, outcomes = "", ()
    resolved_identity = (
        dataclasses.replace(model_identity, effective_model=resolved_parent_model)
        if resolved_parent_model
        else model_identity
    )
    return evidence_session_id, resolved_identity, outcomes
