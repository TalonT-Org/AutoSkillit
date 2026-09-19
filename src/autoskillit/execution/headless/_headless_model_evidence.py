"""Terminal OTLP model-evidence resolution for headless execution."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import anyio

from autoskillit.core import (
    ModelIdentity,
    ProviderOutcome,
    SkillResult,
    SubagentModelOutcomeDict,
    get_logger,
)

if TYPE_CHECKING:
    from autoskillit.execution.evidence.otlp_sink import LocalOtlpSink

logger = get_logger(__name__)


def _reconcile_token_evidence(
    skill_result: SkillResult,
    otlp_token_usage: dict[str, Any] | None,
    provider_outcome: ProviderOutcome,
) -> SkillResult:
    """Select correlated OTLP accounting while retaining parser-only turn metadata."""
    if otlp_token_usage is None:
        return dataclasses.replace(skill_result, provider=provider_outcome)
    selected = dict(otlp_token_usage)
    parser_usage = skill_result.token_usage or {}
    if "turn_count" in parser_usage:
        selected["turn_count"] = parser_usage["turn_count"]
    return dataclasses.replace(skill_result, provider=provider_outcome, token_usage=selected)


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
    backend: str,
    provider_used: str,
) -> tuple[str, ModelIdentity, tuple[SubagentModelOutcomeDict, ...], dict[str, Any] | None]:
    sink_closed = True
    try:
        with anyio.CancelScope(shield=True):
            sink.close()
    except Exception:
        logger.debug("local_otlp_sink_close_failed", exc_info=True)
        sink_closed = False
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
    token_usage = None
    if sink_closed:
        try:
            token_usage = sink.token_usage_for(evidence_session_id, backend, provider_used)
        except Exception:
            logger.warning("local_otlp_sink_token_evidence_failed", exc_info=True)
    return evidence_session_id, resolved_identity, outcomes, token_usage
