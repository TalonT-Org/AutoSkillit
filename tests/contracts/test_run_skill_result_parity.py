"""Keep the live SkillResult JSON projection typed at the MCP boundary."""

from __future__ import annotations

import json

import pytest

from autoskillit.core import (
    ApiFailureOutcome,
    ApiRetryOutcome,
    ContaminationOutcome,
    FaultDomain,
    InfraOutcome,
    KillReason,
    NdjsonDriftOutcome,
    ProviderOutcome,
    RateLimitWindow,
    RetryReason,
    SkillResult,
    WriteEvidence,
)
from autoskillit.server.tools._types import RunSkillResult

pytestmark = pytest.mark.small

_SERVER_ENVELOPE_ONLY = {
    "error",
    "pipeline_tracker",
    "receipt_id",
    "recipe_segment",
    "retriable",
    "stage",
}


def test_run_skill_result_covers_skill_result_projection_bidirectionally() -> None:
    result = SkillResult(
        success=False,
        result="payload",
        session_id="session",
        subtype="api_error",
        is_error=True,
        exit_code=1,
        needs_retry=True,
        retry_reason=RetryReason.RESUME,
        stderr="stderr",
        token_usage={"input_tokens": 1},
        worktree_path="/tmp/worktree",
        branch_name="branch",
        evidence=WriteEvidence(1, True, True, 1),
        kill_reason=KillReason.INFRA_KILL,
        provider=ProviderOutcome("anthropic", True),
        infra=InfraOutcome("api_error", True, FaultDomain.INFRASTRUCTURE),
        api_retry=ApiRetryOutcome(1, "retry", 503, True),
        api_failure=ApiFailureOutcome(
            status=503,
            terminal_reason="api_error",
            error_code="server_error",
            api_error_message_seen=True,
            rate_limit=RateLimitWindow("rejected", "seven_day", 2_000_000_000),
        ),
        contamination=ContaminationOutcome(RetryReason.CLONE_CONTAMINATION, "contaminated"),
        ndjson_drift=NdjsonDriftOutcome(1, 2),
        completion_required=True,
    )
    projection_keys = set(json.loads(result.to_json()))
    typed_keys = set(RunSkillResult.__required_keys__) | set(RunSkillResult.__optional_keys__)

    assert projection_keys <= typed_keys
    assert typed_keys - projection_keys == _SERVER_ENVELOPE_ONLY
