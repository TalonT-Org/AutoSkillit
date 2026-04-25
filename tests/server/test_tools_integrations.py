"""Integration tests for issue lifecycle, headless tool diagnostics, and PR ops."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.core import SkillResult
from autoskillit.core.types import RetryReason
from autoskillit.server.tools_issue_lifecycle import (
    _PREPARE_RESULT_END,
    _PREPARE_RESULT_START,
    claim_issue,
    enrich_issues,
    prepare_issue,
    release_issue,
)
from autoskillit.server.tools_pr_ops import bulk_close_issues, get_pr_reviews
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

# ---------------------------------------------------------------------------
# claim_issue / release_issue / prepare_issue / enrich_issues — gated tools
# ---------------------------------------------------------------------------


class TestClaimIssueTool:
    def test_claim_issue_is_gated(self):
        from autoskillit.pipeline.gate import GATED_TOOLS

        assert "claim_issue" in GATED_TOOLS

    @pytest.mark.anyio
    async def test_claim_issue_returns_gate_error_when_kitchen_closed(self, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_claim_issue_returns_error_without_github_client(self, tool_ctx):
        tool_ctx.github_client = None
        result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.anyio
    async def test_claim_issue_success(self, tool_ctx):
        mock_client = AsyncMock()
        mock_client.fetch_issue.return_value = {"success": True, "labels": []}
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.add_labels.return_value = {"success": True, "labels": ["in-progress"]}
        tool_ctx.github_client = mock_client
        result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
        assert result["success"] is True
        assert result["claimed"] is True
        assert result["issue_number"] == 42

    @pytest.mark.anyio
    async def test_claim_issue_already_claimed(self, tool_ctx):
        mock_client = AsyncMock()
        mock_client.fetch_issue.return_value = {
            "success": True,
            "labels": [{"name": "in-progress"}],
        }
        tool_ctx.github_client = mock_client
        result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
        assert result["success"] is True
        assert result["claimed"] is False

    # P5F4-T1
    @pytest.mark.anyio
    async def test_claim_issue_binds_structlog_context(self, tool_ctx, monkeypatch):
        """claim_issue must bind structlog context vars via bind_contextvars."""
        import structlog

        captured = {}

        def fake_bind_contextvars(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(structlog.contextvars, "bind_contextvars", fake_bind_contextvars)
        monkeypatch.setattr(structlog.contextvars, "clear_contextvars", lambda: None)

        tool_ctx.github_client = None  # triggers early return after bind

        await claim_issue(issue_url="https://github.com/owner/repo/issues/1")
        assert captured == {
            "tool": "claim_issue",
            "issue_url": "https://github.com/owner/repo/issues/1",
        }

    @pytest.mark.anyio
    async def test_claim_issue_allow_reentry_true_returns_claimed_true_when_already_labeled(
        self, tool_ctx
    ):
        """allow_reentry=True with label present: returns claimed=True and reentry=True."""
        mock_client = AsyncMock()
        mock_client.fetch_issue.return_value = {
            "success": True,
            "labels": [{"name": "in-progress"}],
        }
        tool_ctx.github_client = mock_client
        result = json.loads(
            await claim_issue("https://github.com/owner/repo/issues/42", allow_reentry=True)
        )
        assert result["success"] is True
        assert result["claimed"] is True
        assert result["reentry"] is True
        mock_client.add_labels.assert_not_called()  # no re-application needed

    @pytest.mark.anyio
    async def test_claim_issue_allow_reentry_false_returns_claimed_false_when_already_labeled(
        self, tool_ctx
    ):
        """Default allow_reentry=False: claimed=False when label already present."""
        mock_client = AsyncMock()
        mock_client.fetch_issue.return_value = {
            "success": True,
            "labels": [{"name": "in-progress"}],
        }
        tool_ctx.github_client = mock_client
        result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
        assert result["success"] is True
        assert result["claimed"] is False
        assert "reentry" not in result

    @pytest.mark.anyio
    async def test_claim_issue_allow_reentry_true_still_claims_when_label_absent(self, tool_ctx):
        """allow_reentry=True with no pre-existing label performs normal claim."""
        mock_client = AsyncMock()
        mock_client.fetch_issue.return_value = {"success": True, "labels": []}
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.add_labels.return_value = {"success": True, "labels": ["in-progress"]}
        tool_ctx.github_client = mock_client
        result = json.loads(
            await claim_issue("https://github.com/owner/repo/issues/42", allow_reentry=True)
        )
        assert result["success"] is True
        assert result["claimed"] is True
        assert result.get("reentry", False) is False
        mock_client.add_labels.assert_called_once_with("owner", "repo", 42, ["in-progress"])


class TestReleaseIssueTool:
    def test_release_issue_is_gated(self):
        from autoskillit.pipeline.gate import GATED_TOOLS

        assert "release_issue" in GATED_TOOLS

    @pytest.mark.anyio
    async def test_release_issue_returns_gate_error_when_kitchen_closed(self, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await release_issue("https://github.com/owner/repo/issues/42"))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_release_issue_returns_error_without_github_client(self, tool_ctx):
        tool_ctx.github_client = None
        result = json.loads(await release_issue("https://github.com/owner/repo/issues/42"))
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.anyio
    async def test_release_issue_success(self, tool_ctx):
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        tool_ctx.github_client = mock_client
        result = json.loads(await release_issue("https://github.com/owner/repo/issues/42"))
        assert result["success"] is True
        assert result["issue_number"] == 42

    # P5F4-T2
    @pytest.mark.anyio
    async def test_release_issue_binds_structlog_context(self, tool_ctx, monkeypatch):
        """release_issue must bind structlog context vars via bind_contextvars."""
        import structlog

        captured = {}

        def fake_bind_contextvars(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(structlog.contextvars, "bind_contextvars", fake_bind_contextvars)
        monkeypatch.setattr(structlog.contextvars, "clear_contextvars", lambda: None)

        tool_ctx.github_client = None  # triggers early return after bind

        await release_issue(issue_url="https://github.com/owner/repo/issues/1")
        assert captured == {
            "tool": "release_issue",
            "issue_url": "https://github.com/owner/repo/issues/1",
        }


class TestPrepareIssueTool:
    def test_prepare_issue_is_gated(self):
        from autoskillit.pipeline.gate import GATED_TOOLS

        assert "prepare_issue" in GATED_TOOLS

    @pytest.mark.anyio
    async def test_prepare_issue_returns_gate_error_when_kitchen_closed(self, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await prepare_issue("Test title", "Test body"))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_prepare_issue_success_with_result_block(self, tool_ctx):
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
        tool_ctx.executor = mock_executor

        result = json.loads(await prepare_issue("Test title", "Test body"))

        assert result["success"] is True
        assert result["status"] == "complete"
        assert result["issue_number"] == 1
        assert "error" not in result

    @pytest.mark.anyio
    async def test_prepare_issue_success_empty_result_channel_b_drain_race(self, tool_ctx):
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
        tool_ctx.executor = mock_executor

        result = json.loads(await prepare_issue("Test title", "Test body"))

        assert result["success"] is False
        assert result["session_id"] == "sid123"
        assert result["subtype"] == "success"
        assert result["error"] == "session completed but output was empty (drain race)"
        assert result["status"] != "complete"  # contradiction must be impossible

    @pytest.mark.anyio
    async def test_prepare_issue_failure_with_diagnostics(self, tool_ctx):
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
        tool_ctx.executor = mock_executor

        result = json.loads(await prepare_issue("Test title", "Test body"))

        assert result["success"] is False
        assert result["session_id"] == "sid456"
        assert result["stderr"] == "Claude exited unexpectedly"
        assert result["subtype"] == "missing_completion_marker"
        assert result["exit_code"] == 1

    @pytest.mark.anyio
    async def test_prepare_issue_passes_expected_output_patterns_to_executor(self, tool_ctx):
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
        tool_ctx.executor = mock_executor
        tool_ctx.output_pattern_resolver = lambda cmd: ["---prepare-issue-result---"]

        await prepare_issue("Title", "Body")

        call_kwargs = mock_executor.run.call_args.kwargs
        assert call_kwargs.get("expected_output_patterns") == ["---prepare-issue-result---"]

    @pytest.mark.anyio
    async def test_prepare_issue_response_success_field_never_overwritten_by_parsed_spread(
        self, tool_ctx
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
        tool_ctx.executor = mock_executor

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
        self, tool_ctx, skill_success, skill_result_text
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
        tool_ctx.executor = mock_executor

        result = json.loads(await prepare_issue("Title", "Body"))

        assert result["success"] is False
        assert result["status"] == "failed"

    @pytest.mark.anyio
    async def test_prepare_issue_no_result_block_includes_stderr(self, tool_ctx):
        """success=True + non-empty result + no delimiters → stderr surfaced."""
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
        tool_ctx.executor = mock_executor
        response = json.loads(await prepare_issue("Test Issue", ""))
        assert response["success"] is False
        assert response["error"] == "no result block found"
        assert "stderr" in response, "stderr must be in block-parse-failure response"
        assert response["stderr"] == "ImportError: cannot import x from autoskillit"
        assert response["session_id"] == "abc-123"

    @pytest.mark.anyio
    async def test_prepare_issue_empty_output_includes_stderr(self, tool_ctx):
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
        tool_ctx.executor = mock_executor
        response = json.loads(await prepare_issue("Test Issue", ""))
        assert response["success"] is False
        assert "drain race" in response["error"]
        assert "stderr" in response, "stderr must be in drain-race failure response"
        assert response["stderr"] == "Connection reset by peer"
        assert response["session_id"] == "abc-456"

    @pytest.mark.anyio
    async def test_prepare_issue_session_failure_uses_subtype_not_block_sentinel(self, tool_ctx):
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
        tool_ctx.executor = mock_executor
        response = json.loads(await prepare_issue("Test Issue", ""))
        assert response["success"] is False
        assert response["error"] != "no result block found", (
            "Wrong-branch masking: failure path must not call _parse_prepare_result"
        )
        assert response["subtype"] == "stale"


class TestEnrichIssuesTool:
    def test_enrich_issues_is_gated(self):
        from autoskillit.pipeline.gate import GATED_TOOLS

        assert "enrich_issues" in GATED_TOOLS

    @pytest.mark.anyio
    async def test_enrich_issues_returns_gate_error_when_kitchen_closed(self, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await enrich_issues())
        assert result["success"] is False
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_enrich_issues_success_empty_result_includes_diagnostics(self, tool_ctx):
        """Drain race for enrich_issues: success=True with empty result must yield failure."""
        mock_executor = AsyncMock()
        mock_executor.run.return_value = SkillResult(
            success=True,
            result="",
            session_id="sid789",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        tool_ctx.executor = mock_executor

        result = json.loads(await enrich_issues())

        assert result["success"] is False
        assert result["session_id"] == "sid789"

    @pytest.mark.anyio
    async def test_enrich_issues_failure_includes_session_id_and_stderr(self, tool_ctx):
        """Executor failure: response includes session_id and stderr for diagnosis."""
        mock_executor = AsyncMock()
        mock_executor.run.return_value = SkillResult(
            success=False,
            result="",
            session_id="sid-fail",
            subtype="missing_completion_marker",
            is_error=True,
            exit_code=2,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="Session timed out",
        )
        tool_ctx.executor = mock_executor

        result = json.loads(await enrich_issues())

        assert result["success"] is False
        assert result["session_id"] == "sid-fail"
        assert result["stderr"] == "Session timed out"

    @pytest.mark.anyio
    async def test_enrich_issues_passes_expected_output_patterns_to_executor(self, tool_ctx):
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
        tool_ctx.executor = mock_executor
        tool_ctx.output_pattern_resolver = lambda cmd: ["---enrich-issues-result---"]

        await enrich_issues()

        call_kwargs = mock_executor.run.call_args.kwargs
        assert call_kwargs.get("expected_output_patterns") == ["---enrich-issues-result---"]

    @pytest.mark.anyio
    async def test_enrich_issues_no_result_block_includes_stderr(self, tool_ctx):
        """success=True + non-empty result + no delimiters → stderr surfaced."""
        mock_executor = AsyncMock()
        mock_executor.run.return_value = SkillResult(
            success=True,
            result="All issues enriched. Workflow complete.",
            session_id="enrich-123",
            stderr="Warning: contract stale",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
        )
        tool_ctx.executor = mock_executor
        response = json.loads(await enrich_issues())
        assert response["success"] is False
        assert response["error"] == "no result block found"
        assert "stderr" in response, "stderr must be in block-parse-failure response"
        assert response["stderr"] == "Warning: contract stale"
        assert response["session_id"] == "enrich-123"


_REQUIRED_FAILURE_KEYS = frozenset(
    {"success", "error", "session_id", "stderr", "subtype", "exit_code"}
)

# Intentional scope: only prepare_issue and enrich_issues call
# _build_headless_error_response. claim_issue, release_issue, and report_bug
# use separate error-response paths and are covered by their own tests.
_HEADLESS_FAILURE_SCENARIOS = [
    pytest.param(
        "prepare_issue",
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
        "prepare_issue",
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
    pytest.param(
        "prepare_issue",
        dict(
            success=True,
            result="prose without delimiters",
            session_id="s3",
            stderr="e3",
            subtype="success",
            exit_code=0,
            needs_retry=False,
            is_error=False,
            retry_reason=RetryReason.NONE,
        ),
        id="prepare_issue-block_parse_error",
    ),
    pytest.param(
        "enrich_issues",
        dict(
            success=False,
            result="",
            session_id="s4",
            stderr="e4",
            subtype="stale",
            exit_code=-1,
            needs_retry=True,
            is_error=True,
            retry_reason=RetryReason.RESUME,
        ),
        id="enrich_issues-session_failed",
    ),
    pytest.param(
        "enrich_issues",
        dict(
            success=True,
            result="",
            session_id="s5",
            stderr="e5",
            subtype="success",
            exit_code=0,
            needs_retry=False,
            is_error=False,
            retry_reason=RetryReason.NONE,
        ),
        id="enrich_issues-drain_race",
    ),
    pytest.param(
        "enrich_issues",
        dict(
            success=True,
            result="prose without delimiters",
            session_id="s6",
            stderr="e6",
            subtype="success",
            exit_code=0,
            needs_retry=False,
            is_error=False,
            retry_reason=RetryReason.NONE,
        ),
        id="enrich_issues-block_parse_error",
    ),
]


@pytest.mark.anyio
@pytest.mark.parametrize("tool_name,skill_result_kwargs", _HEADLESS_FAILURE_SCENARIOS)
async def test_headless_tool_failure_paths_include_all_diagnostic_fields(
    tool_name, skill_result_kwargs, tool_ctx
):
    """Contract test: every failure path of every headless session tool
    must surface the full diagnostic set: success, error, session_id,
    stderr, subtype, exit_code.
    """
    tool_fn = {"prepare_issue": prepare_issue, "enrich_issues": enrich_issues}[tool_name]
    mock_executor = AsyncMock()
    mock_executor.run.return_value = SkillResult(**skill_result_kwargs)
    tool_ctx.executor = mock_executor

    kwargs: dict = {}
    if tool_name == "prepare_issue":
        kwargs = {"title": "Test Issue", "body": ""}

    response = json.loads(await tool_fn(**kwargs))
    missing = _REQUIRED_FAILURE_KEYS - set(response.keys())
    assert not missing, f"tool={tool_name!r} missing failure response keys: {missing}"
    assert response["success"] is False
    assert response["stderr"] == skill_result_kwargs["stderr"]
    assert response["session_id"] == skill_result_kwargs["session_id"]


class TestGetPrReviews:
    @pytest.mark.anyio
    async def test_returns_structured_reviews(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0,
                json.dumps(
                    [
                        {"user": {"login": "reviewer1"}, "state": "APPROVED", "body": "LGTM"},
                        {
                            "user": {"login": "reviewer2"},
                            "state": "CHANGES_REQUESTED",
                            "body": "Fix this",
                        },
                    ]
                ),
                "",
            )
        )
        result = json.loads(await get_pr_reviews(42, ".", repo="owner/repo"))
        assert len(result["reviews"]) == 2
        assert result["reviews"][0] == {"author": "reviewer1", "state": "APPROVED", "body": "LGTM"}

    @pytest.mark.anyio
    async def test_empty_reviews(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, json.dumps([]), ""))
        result = json.loads(await get_pr_reviews(42, ".", repo="owner/repo"))
        assert result["reviews"] == []

    @pytest.mark.anyio
    async def test_gh_command_failure_returns_error(self, tool_ctx):
        tool_ctx.runner.push(_make_result(1, "", "could not find PR"))
        result = json.loads(await get_pr_reviews(99, ".", repo="owner/repo"))
        assert result["success"] is False

    @pytest.mark.anyio
    async def test_without_repo_uses_pr_view(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0,
                json.dumps(
                    {
                        "reviews": [
                            {"author": {"login": "x"}, "state": "APPROVED", "body": ""},
                        ]
                    }
                ),
                "",
            )
        )
        result = json.loads(await get_pr_reviews(42, "."))
        assert result["reviews"][0]["author"] == "x"

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx):
        tool_ctx.gate.disable()
        result = json.loads(await get_pr_reviews(1, "."))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"


class TestBulkCloseIssues:
    @pytest.mark.anyio
    async def test_closes_all_issues_successfully(self, tool_ctx):
        for _ in range(3):
            tool_ctx.runner.push(_make_result(0, "", ""))
        result = json.loads(await bulk_close_issues([1, 2, 3], "", "."))
        assert result["closed"] == [1, 2, 3]
        assert result["failed"] == []

    @pytest.mark.anyio
    async def test_partial_failure_tracked_per_issue(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(1, "", "not found"))
        tool_ctx.runner.push(_make_result(0, "", ""))
        result = json.loads(await bulk_close_issues([1, 2, 3], "", "."))
        assert result["closed"] == [1, 3]
        assert result["failed"] == [2]

    @pytest.mark.anyio
    async def test_empty_numbers_list(self, tool_ctx):
        result = json.loads(await bulk_close_issues([], "", "."))
        assert result == {"closed": [], "failed": []}

    @pytest.mark.anyio
    async def test_comment_appended_to_body_when_provided(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "existing body", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        result = json.loads(await bulk_close_issues([7], "Closed by pipeline.", "."))
        all_cmds = [call[0] for call in tool_ctx.runner.call_args_list]
        edit_calls = [cmd for cmd in all_cmds if "edit" in cmd]
        assert any("--body-file" in cmd for cmd in edit_calls), (
            "Expected gh issue edit --body-file call"
        )
        assert result["closed"] == [7]

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx):
        tool_ctx.gate.disable()
        result = json.loads(await bulk_close_issues([1], "", "."))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"
