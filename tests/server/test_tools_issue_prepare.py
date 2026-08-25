"""Tests for the prepare_issue MCP tool."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.core import SkillResult
from autoskillit.core.types import RetryReason
from autoskillit.server.tools.tools_issue_headless import (
    _PREPARE_RESULT_END,
    _PREPARE_RESULT_START,
    prepare_issue,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestPrepareIssueTool:
    def test_prepare_issue_is_gated(self):
        from autoskillit.pipeline.gate import GATED_TOOLS

        assert "prepare_issue" in GATED_TOOLS

    @pytest.mark.anyio
    async def test_prepare_issue_success_with_result_block(self, tool_ctx_kitchen_open):
        """Happy path: executor returns success=True with a valid result block."""
        result_text = (
            f"{_PREPARE_RESULT_START}\n"
            '{"issue_url": "https://github.com/o/r/issues/1", "issue_number": 1, '
            '"route": "recipe:implementation", "issue_type": "enhancement", '
            '"confidence": 0.9, "rationale": "ok", "labels_applied": [], '
            '"dry_run": false, "sub_issues": []}\n'
            f"{_PREPARE_RESULT_END}"
        )
        mock_executor = AsyncMock()
        mock_executor.run.return_value = SkillResult(
            success=True,
            result=result_text,
            session_id="sid123",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        tool_ctx_kitchen_open.executor = mock_executor

        result = json.loads(await prepare_issue("Test title", "Test body"))

        assert result["success"] is True
        assert result["status"] == "complete"
        assert result["issue_number"] == 1
        assert "error" not in result

    @pytest.mark.anyio
    async def test_prepare_issue_success_empty_result_channel_b_drain_race(
        self, tool_ctx_kitchen_open
    ):
        """Channel B drain race: executor returns success=True but result is empty.
        Response must be success=False with diagnostics — THE KEY CONTRADICTION TEST.
        """
        mock_executor = AsyncMock()
        mock_executor.run.return_value = SkillResult(
            success=True,
            result="",
            session_id="sid123",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        tool_ctx_kitchen_open.executor = mock_executor

        result = json.loads(await prepare_issue("Test title", "Test body"))

        assert result["success"] is False
        assert result["session_id"] == "sid123"
        assert result["subtype"] == "success"
        assert result["error"] == "session completed but output was empty (drain race)"
        assert result["status"] != "complete"  # contradiction must be impossible

    @pytest.mark.anyio
    async def test_prepare_issue_failure_with_diagnostics(self, tool_ctx_kitchen_open):
        """Executor failure: response must surface session_id, stderr, subtype, exit_code."""
        mock_executor = AsyncMock()
        mock_executor.run.return_value = SkillResult(
            success=False,
            result="",
            session_id="sid456",
            subtype="missing_completion_marker",
            is_error=True,
            exit_code=1,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="Claude exited unexpectedly",
        )
        tool_ctx_kitchen_open.executor = mock_executor

        result = json.loads(await prepare_issue("Test title", "Test body"))

        assert result["success"] is False
        assert result["session_id"] == "sid456"
        assert result["stderr"] == "Claude exited unexpectedly"
        assert result["subtype"] == "missing_completion_marker"
        assert result["exit_code"] == 1

    @pytest.mark.anyio
    async def test_prepare_issue_passes_expected_output_patterns_to_executor(
        self, tool_ctx_kitchen_open
    ):
        """output_pattern_resolver is consulted and patterns are passed to executor.run()."""
        mock_executor = AsyncMock()
        mock_executor.run.return_value = SkillResult(
            success=False,
            result="",
            session_id="sid",
            subtype="error",
            is_error=True,
            exit_code=1,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        tool_ctx_kitchen_open.executor = mock_executor
        tool_ctx_kitchen_open.output_pattern_resolver = lambda cmd: ["---prepare-issue-result---"]

        await prepare_issue("Title", "Body")

        call_kwargs = mock_executor.run.call_args.kwargs
        assert call_kwargs.get("expected_output_patterns") == ["---prepare-issue-result---"]
        capability_contract = call_kwargs["capability_contract"]
        assert not hasattr(capability_contract, "resolved_command")
        assert mock_executor.run.call_args.args[0].startswith("/prepare-issue")
        assert capability_contract.cwd == str(tool_ctx_kitchen_open.project_dir.resolve())
        assert capability_contract.member_names == ("prepare-issue",)

    @pytest.mark.anyio
    async def test_prepare_issue_response_success_field_never_overwritten_by_parsed_spread(
        self, tool_ctx_kitchen_open
    ):
        """When parsed block contains 'success': false, the outer success=True is preserved."""
        result_text = (
            f"{_PREPARE_RESULT_START}\n"
            '{"success": false, "error": "skill-internal error"}\n'
            f"{_PREPARE_RESULT_END}"
        )
        mock_executor = AsyncMock()
        mock_executor.run.return_value = SkillResult(
            success=True,
            result=result_text,
            session_id="sid",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        tool_ctx_kitchen_open.executor = mock_executor

        result = json.loads(await prepare_issue("Title", "Body"))

        assert result["success"] is True
        assert result["status"] == "complete"

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "skill_success,skill_result_text",
        [
            (True, ""),  # drain race: session ok but no output
            (False, ""),  # session failure
        ],
    )
    async def test_prepare_issue_contradictory_state_is_impossible(
        self, tool_ctx_kitchen_open, skill_success, skill_result_text
    ):
        """status=complete and success=False must never co-exist in any response."""
        mock_executor = AsyncMock()
        mock_executor.run.return_value = SkillResult(
            success=skill_success,
            result=skill_result_text,
            session_id="sid",
            subtype="success" if skill_success else "error",
            is_error=not skill_success,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        tool_ctx_kitchen_open.executor = mock_executor

        result = json.loads(await prepare_issue("Title", "Body"))

        assert result["success"] is False
        assert result["status"] == "failed"

    @pytest.mark.anyio
    async def test_prepare_issue_no_result_block_includes_stderr(self, tool_ctx_kitchen_open):
        """success=True + non-empty result + no delimiters → degraded success."""
        mock_executor = AsyncMock()
        mock_executor.run.return_value = SkillResult(
            success=True,
            result="I created the issue. All steps complete.",
            session_id="abc-123",
            stderr="ImportError: cannot import x from autoskillit",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
        )
        tool_ctx_kitchen_open.executor = mock_executor
        response = json.loads(await prepare_issue("Test Issue", ""))
        assert response["success"] is True
        assert response["status"] == "degraded"
        assert response["warning"] == "no result block found"
        assert "stderr" in response, "stderr must be in degraded-success response"
        assert response["stderr"] == "ImportError: cannot import x from autoskillit"
        assert response["session_id"] == "abc-123"

    @pytest.mark.anyio
    async def test_prepare_issue_empty_output_includes_stderr(self, tool_ctx_kitchen_open):
        """success=True + empty result (drain race) → stderr surfaced."""
        mock_executor = AsyncMock()
        mock_executor.run.return_value = SkillResult(
            success=True,
            result="",
            session_id="abc-456",
            stderr="Connection reset by peer",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
        )
        tool_ctx_kitchen_open.executor = mock_executor
        response = json.loads(await prepare_issue("Test Issue", ""))
        assert response["success"] is False
        assert "drain race" in response["error"]
        assert "stderr" in response, "stderr must be in drain-race failure response"
        assert response["stderr"] == "Connection reset by peer"
        assert response["session_id"] == "abc-456"

    @pytest.mark.anyio
    async def test_prepare_issue_session_failure_uses_subtype_not_block_sentinel(
        self, tool_ctx_kitchen_open
    ):
        """success=False must NOT call _parse_prepare_result.
        The error must reflect actual failure reason, not 'no result block found'.
        """
        mock_executor = AsyncMock()
        mock_executor.run.return_value = SkillResult(
            success=False,
            result="Session context exhausted. Cannot continue.",
            session_id="abc-789",
            stderr="",
            subtype="stale",
            is_error=True,
            exit_code=-1,
            needs_retry=True,
            retry_reason=RetryReason.RESUME,
        )
        tool_ctx_kitchen_open.executor = mock_executor
        response = json.loads(await prepare_issue("Test Issue", ""))
        assert response["success"] is False
        assert response["error"] != "no result block found", (
            "Wrong-branch masking: failure path must not call _parse_prepare_result"
        )
        assert response["subtype"] == "stale"


_REQUIRED_FAILURE_KEYS = frozenset(
    {"success", "error", "session_id", "stderr", "subtype", "exit_code"}
)

# prepare_issue is the only headless session tool that calls
# _build_headless_error_response; claim_issue, release_issue, and report_bug
# use separate error-response paths and are covered by their own tests.
_PREPARE_FAILURE_SCENARIOS = [
    pytest.param(
        dict(
            success=False,
            result="",
            session_id="s1",
            stderr="e1",
            subtype="stale",
            exit_code=-1,
            needs_retry=True,
            is_error=True,
            retry_reason=RetryReason.RESUME,
        ),
        id="prepare_issue-session_failed",
    ),
    pytest.param(
        dict(
            success=True,
            result="",
            session_id="s2",
            stderr="e2",
            subtype="success",
            exit_code=0,
            needs_retry=False,
            is_error=False,
            retry_reason=RetryReason.NONE,
        ),
        id="prepare_issue-drain_race",
    ),
]


@pytest.mark.anyio
@pytest.mark.parametrize("skill_result_kwargs", _PREPARE_FAILURE_SCENARIOS)
async def test_headless_tool_failure_paths_include_all_diagnostic_fields(
    skill_result_kwargs, tool_ctx_kitchen_open
):
    """Contract test: every failure path of prepare_issue must surface the
    full diagnostic set: success, error, session_id, stderr, subtype, exit_code.
    """
    mock_executor = AsyncMock()
    mock_executor.run.return_value = SkillResult(**skill_result_kwargs)
    tool_ctx_kitchen_open.executor = mock_executor

    response = json.loads(await prepare_issue(title="Test Issue", body=""))
    missing = _REQUIRED_FAILURE_KEYS - set(response.keys())
    assert not missing, f"prepare_issue missing failure response keys: {missing}"
    assert response["success"] is False
    assert response["stderr"] == skill_result_kwargs["stderr"]
    assert response["session_id"] == skill_result_kwargs["session_id"]


@pytest.mark.anyio
async def test_prepare_issue_contract_recovery_propagates_partial_url(
    tool_ctx_kitchen_open,
):
    """CONTRACT_RECOVERY: prepare_issue surfaces partial_issue_url alongside canonical fields."""
    mock_executor = AsyncMock()
    mock_executor.run.return_value = SkillResult(
        success=False,
        result="Created issue\nhttps://github.com/owner/repo/issues/42\nNow labeling...",
        session_id="s-contract",
        stderr="e-contract",
        subtype="contract_recovery",
        is_error=True,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.CONTRACT_RECOVERY,
    )
    tool_ctx_kitchen_open.executor = mock_executor

    response = json.loads(await prepare_issue("Title", "Body"))
    # Canonical contract preserved.
    missing = _REQUIRED_FAILURE_KEYS - set(response.keys())
    assert not missing, f"missing failure response keys: {missing}"
    assert response["success"] is False
    # Partial-result propagation.
    assert response["partial_issue_url"] == "https://github.com/owner/repo/issues/42"
    assert response["partial_issue_number"] == 42


@pytest.mark.anyio
async def test_prepare_issue_block_parse_error_propagates_partial_url(
    tool_ctx_kitchen_open,
):
    """Block-parse-error path: prepare_issue surfaces partial_issue_url from result text."""
    mock_executor = AsyncMock()
    mock_executor.run.return_value = SkillResult(
        success=True,
        result=(
            "Created https://github.com/owner/repo/issues/42\n"
            "---prepare-issue-result---\n"
            "{bad json\n"
            "---/prepare-issue-result---"
        ),
        session_id="s-parse",
        stderr="e-parse",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
    )
    tool_ctx_kitchen_open.executor = mock_executor

    response = json.loads(await prepare_issue("Title", "Body"))
    # Degraded-success contract preserves adjudication authority and diagnostics.
    assert response["success"] is True
    assert response["status"] == "degraded"
    assert "invalid JSON" in response["warning"]
    assert response["session_id"] == "s-parse"
    assert response["stderr"] == "e-parse"
    # Partial-result propagation.
    assert response["partial_issue_url"] == "https://github.com/owner/repo/issues/42"
    assert response["partial_issue_number"] == 42
