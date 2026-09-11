"""Split integrity tests for execution/headless/ _headless_evidence split.

Verifies that symbols moved to _headless_evidence are importable.
"""

import pytest

from autoskillit.core import RetryReason, SkillResult
from autoskillit.execution.session._turn_usage import build_turn_token_entry

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestHeadlessEvidenceModuleExists:
    """Symbols moved to _headless_evidence are importable from there."""

    def test__adapt_agent_result_importable(self):
        from autoskillit.execution.headless._headless_evidence import _adapt_agent_result

        assert callable(_adapt_agent_result)

    def test__compute_write_evidence_importable(self):
        from autoskillit.execution.headless._headless_evidence import _compute_write_evidence

        assert callable(_compute_write_evidence)

    def test__build_session_telemetry_importable(self):
        from autoskillit.execution.headless._headless_evidence import _build_session_telemetry

        assert callable(_build_session_telemetry)

    def test_build_session_telemetry_preserves_turn_usage(self):
        from autoskillit.execution.headless._headless_evidence import _build_session_telemetry

        turn_usage = [
            build_turn_token_entry(
                backend="codex",
                request_id="request-1",
                cache_read_tokens=40,
                context_window_tokens=200_000,
            )
        ]
        skill_result = SkillResult(
            success=True,
            result="done",
            session_id="session-1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
            turn_usage=turn_usage,
        )

        telemetry = _build_session_telemetry(
            skill_result=skill_result,
            timing_seconds=1.0,
            audit_record=None,
            github_api_log=None,
            loc_insertions=0,
            loc_deletions=0,
            session_id="session-1",
            subagent_model_outcomes=(),
            child_outcomes=(),
        )

        assert telemetry.turn_usage == turn_usage

    def test__capture_failure_importable(self):
        from autoskillit.execution.headless._headless_evidence import _capture_failure

        assert callable(_capture_failure)
