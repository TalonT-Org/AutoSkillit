"""Tests for server/tools_issue_lifecycle.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.core import RetryReason, SkillResult
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools.tools_issue_lifecycle import (
    _build_enrich_skill_command,
    _build_headless_error_response,
    _build_prepare_skill_command,
    _extract_label_names,
    _parse_enrich_result,
    _parse_prepare_result,
    _retry_reason_to_error,
    _without_success_key,
    claim_issue,
    enrich_issues,
    prepare_issue,
    release_issue,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_skill_result(
    success: bool = True,
    result: str = "",
    subtype: str = "",
    retry_reason: RetryReason = RetryReason.NONE,
    exit_code: int = 0,
    stderr: str = "",
    session_id: str = "sess-1",
) -> SkillResult:
    return SkillResult(
        success=success,
        result=result,
        session_id=session_id,
        subtype=subtype,
        is_error=not success,
        exit_code=exit_code,
        needs_retry=False,
        retry_reason=retry_reason,
        stderr=stderr,
        token_usage=None,
    )


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def test_build_headless_error_response_fields() -> None:
    """All required fields present in error response: success, status, error, session_id, etc."""
    result = _make_skill_result(success=False, session_id="abc", subtype="timeout", exit_code=1)
    resp = _build_headless_error_response(result, error="Something failed")
    assert resp["success"] is False
    assert resp["status"] == "failed"
    assert resp["error"] == "Something failed"
    assert resp["session_id"] == "abc"
    assert resp["stderr"] == ""
    assert resp["subtype"] == "timeout"
    assert resp["exit_code"] == 1


def test_retry_reason_to_error_uses_enum_value() -> None:
    """Non-NONE RetryReason → returns its .value string."""
    result = _make_skill_result(success=False, retry_reason=RetryReason.STALE)
    assert _retry_reason_to_error(result) == RetryReason.STALE.value


def test_retry_reason_to_error_falls_back_to_subtype() -> None:
    """RetryReason.NONE with subtype='context_exhausted' → returns 'context_exhausted'."""
    result = _make_skill_result(
        success=False, retry_reason=RetryReason.NONE, subtype="context_exhausted"
    )
    assert _retry_reason_to_error(result) == "context_exhausted"


def test_extract_label_names_dicts_and_strings() -> None:
    """[{"name": "bug"}, "enhancement"] → ["bug", "enhancement"]."""
    assert _extract_label_names([{"name": "bug"}, "enhancement"]) == ["bug", "enhancement"]


def test_without_success_key_removes_it() -> None:
    """{"success": True, "x": 1} → {"x": 1}."""
    assert _without_success_key({"success": True, "x": 1}) == {"x": 1}


def test_without_success_key_no_success_is_noop() -> None:
    """{"x": 1} → {"x": 1} (no change when key absent)."""
    assert _without_success_key({"x": 1}) == {"x": 1}


def test_build_prepare_skill_command_basic() -> None:
    """No labels, no dry_run → '/prepare-issue\\n\\nTitle: T\\n\\nBody:\\nB'."""
    cmd = _build_prepare_skill_command("T", "B", "", None, False, False)
    assert cmd == "/prepare-issue\n\nTitle: T\n\nBody:\nB"


def test_build_prepare_skill_command_with_flags() -> None:
    """labels + dry_run + split → all flags in output."""
    cmd = _build_prepare_skill_command("T", "B", "owner/repo", ["bug", "needs-triage"], True, True)
    assert "--repo owner/repo" in cmd
    assert "--label bug" in cmd
    assert "--label needs-triage" in cmd
    assert "--dry-run" in cmd
    assert "--split" in cmd


def test_parse_prepare_result_success() -> None:
    """Text with delimiters surrounding valid JSON → parsed dict."""
    payload = json.dumps({"issue_url": "https://github.com/owner/repo/issues/1"})
    text = f"some preamble\n---prepare-issue-result---\n{payload}\n---/prepare-issue-result---\n"
    result = _parse_prepare_result(text)
    assert result["issue_url"] == "https://github.com/owner/repo/issues/1"


def test_parse_prepare_result_no_block() -> None:
    """Text without delimiters → {"success": False, "error": "no result block found"}."""
    result = _parse_prepare_result("no delimiters here")
    assert result == {"success": False, "error": "no result block found"}


def test_parse_prepare_result_invalid_json() -> None:
    """Block contains non-JSON → error dict with 'result block contained invalid JSON'."""
    text = "---prepare-issue-result---\nnot valid json\n---/prepare-issue-result---\n"
    result = _parse_prepare_result(text)
    assert result == {"success": False, "error": "result block contained invalid JSON"}


def test_build_enrich_skill_command_with_all_args() -> None:
    """All args provided → assembled command includes all flags."""
    cmd = _build_enrich_skill_command(42, 3, True, "owner/repo")
    assert "/enrich-issues" in cmd
    assert "--issue 42" in cmd
    assert "--batch 3" in cmd
    assert "--dry-run" in cmd
    assert "--repo owner/repo" in cmd


def test_parse_enrich_result_success() -> None:
    """Text with delimiters surrounding valid JSON → parsed dict."""
    payload = json.dumps({"enriched": [1, 2]})
    text = f"---enrich-issues-result---\n{payload}\n---/enrich-issues-result---\n"
    result = _parse_enrich_result(text)
    assert result["enriched"] == [1, 2]


def test_parse_enrich_result_no_block() -> None:
    """Text without delimiters → error dict."""
    result = _parse_enrich_result("nothing")
    assert result == {"success": False, "error": "no result block found"}


# ---------------------------------------------------------------------------
# MCP tool handlers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_prepare_issue_gate_closed(tool_ctx) -> None:
    """Gate disabled → gate error JSON."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await prepare_issue("Title", "Body"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_prepare_issue_no_executor(tool_ctx) -> None:
    """executor=None → {"success": False, "error": "Executor not configured"}."""
    tool_ctx.executor = None
    result = json.loads(await prepare_issue("Title", "Body"))
    assert result["success"] is False
    assert "Executor not configured" in result["error"]


@pytest.mark.anyio
async def test_prepare_issue_session_failure(tool_ctx) -> None:
    """executor.run → success=False → error response with diagnostic fields."""
    skill_result = _make_skill_result(
        success=False, subtype="timeout", exit_code=1, stderr="process killed"
    )
    tool_ctx.executor = AsyncMock()
    tool_ctx.executor.run = AsyncMock(return_value=skill_result)

    result = json.loads(await prepare_issue("Title", "Body"))
    assert result["success"] is False
    assert "session_id" in result
    assert "stderr" in result


@pytest.mark.anyio
async def test_prepare_issue_empty_output(tool_ctx) -> None:
    """success=True but result="" → drain-race error."""
    skill_result = _make_skill_result(success=True, result="")
    tool_ctx.executor = AsyncMock()
    tool_ctx.executor.run = AsyncMock(return_value=skill_result)

    result = json.loads(await prepare_issue("Title", "Body"))
    assert result["success"] is False
    assert "drain race" in result["error"]


@pytest.mark.anyio
async def test_prepare_issue_block_parse_error(tool_ctx) -> None:
    """success=True, output present, but no delimiters → 'no result block found' error."""
    skill_result = _make_skill_result(success=True, result="some output without delimiters")
    tool_ctx.executor = AsyncMock()
    tool_ctx.executor.run = AsyncMock(return_value=skill_result)

    result = json.loads(await prepare_issue("Title", "Body"))
    assert result["success"] is False
    assert result["error"] == "no result block found"


@pytest.mark.anyio
async def test_prepare_issue_success(tool_ctx) -> None:
    """Complete success path → success=True, block fields merged without 'success' key conflict."""
    block_data = {"issue_url": "https://github.com/o/r/issues/1", "issue_number": 1}
    payload = json.dumps(block_data)
    output = f"---prepare-issue-result---\n{payload}\n---/prepare-issue-result---\n"
    skill_result = _make_skill_result(success=True, result=output)
    tool_ctx.executor = AsyncMock()
    tool_ctx.executor.run = AsyncMock(return_value=skill_result)

    result = json.loads(await prepare_issue("Title", "Body"))
    assert result["success"] is True
    assert result["status"] == "complete"
    assert result["issue_url"] == "https://github.com/o/r/issues/1"


@pytest.mark.anyio
async def test_enrich_issues_gate_closed(tool_ctx) -> None:
    """Gate disabled → gate error JSON."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await enrich_issues())
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_enrich_issues_success(tool_ctx) -> None:
    """Successful enrich → success=True, enriched list in response."""
    block_data = {"enriched": [42], "skipped_already_enriched": []}
    payload = json.dumps(block_data)
    output = f"---enrich-issues-result---\n{payload}\n---/enrich-issues-result---\n"
    skill_result = _make_skill_result(success=True, result=output)
    tool_ctx.executor = AsyncMock()
    tool_ctx.executor.run = AsyncMock(return_value=skill_result)

    result = json.loads(await enrich_issues())
    assert result["success"] is True
    assert result["enriched"] == [42]


@pytest.mark.anyio
async def test_claim_issue_gate_closed(tool_ctx) -> None:
    """Gate disabled → gate error JSON."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_claim_issue_no_client(tool_ctx) -> None:
    """github_client=None → error response."""
    tool_ctx.github_client = None
    result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
    assert result["success"] is False
    assert "token" in result["error"].lower() or "github" in result["error"].lower()


@pytest.mark.anyio
async def test_claim_issue_already_claimed_returns_not_claimed(
    tool_ctx,
) -> None:
    """Label already present, allow_reentry=False → claimed=False."""
    issue_data = {
        "success": True,
        "labels": [{"name": "autoskillit:in-progress"}],
    }
    tool_ctx.github_client = AsyncMock()
    tool_ctx.github_client.fetch_issue = AsyncMock(return_value=issue_data)

    result = json.loads(
        await claim_issue(
            "https://github.com/owner/repo/issues/42",
            label="autoskillit:in-progress",
            allow_reentry=False,
        )
    )
    assert result["success"] is True
    assert result["claimed"] is False


@pytest.mark.anyio
async def test_claim_issue_reentry_allowed(tool_ctx) -> None:
    """Label already present, allow_reentry=True → claimed=True, reentry=True."""
    issue_data = {
        "success": True,
        "labels": [{"name": "autoskillit:in-progress"}],
    }
    tool_ctx.github_client = AsyncMock()
    tool_ctx.github_client.fetch_issue = AsyncMock(return_value=issue_data)

    result = json.loads(
        await claim_issue(
            "https://github.com/owner/repo/issues/42",
            label="autoskillit:in-progress",
            allow_reentry=True,
        )
    )
    assert result["success"] is True
    assert result["claimed"] is True
    assert result.get("reentry") is True


@pytest.mark.anyio
async def test_claim_issue_success(tool_ctx) -> None:
    """Label not present → applies label, claimed=True."""
    issue_data = {"success": True, "labels": []}
    tool_ctx.github_client = AsyncMock()
    tool_ctx.github_client.fetch_issue = AsyncMock(return_value=issue_data)
    tool_ctx.github_client.ensure_label = AsyncMock(return_value={"success": True})
    tool_ctx.github_client.swap_labels = AsyncMock(return_value={"success": True})

    result = json.loads(
        await claim_issue(
            "https://github.com/owner/repo/issues/42",
            label="autoskillit:in-progress",
        )
    )
    assert result["success"] is True
    assert result["claimed"] is True
    assert result["issue_number"] == 42


@pytest.mark.anyio
async def test_release_issue_gate_closed(tool_ctx) -> None:
    """Gate disabled → gate error JSON."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await release_issue("https://github.com/owner/repo/issues/42"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_release_issue_no_staging_when_same_branch(
    tool_ctx,
) -> None:
    """target_branch == promotion_target → staged=False."""
    tool_ctx.github_client = AsyncMock()
    tool_ctx.github_client.remove_label = AsyncMock(return_value={"success": True})
    promotion_target = tool_ctx.config.branching.promotion_target

    result = json.loads(
        await release_issue(
            "https://github.com/owner/repo/issues/42",
            label="autoskillit:in-progress",
            target_branch=promotion_target,
        )
    )
    assert result["success"] is True
    assert result["staged"] is False


@pytest.mark.anyio
async def test_release_issue_stages_when_different_branch(
    tool_ctx,
) -> None:
    """target_branch != promotion_target → staged=True, staged_label applied."""
    tool_ctx.github_client = AsyncMock()
    tool_ctx.github_client.remove_label = AsyncMock(return_value={"success": True})
    tool_ctx.github_client.ensure_label = AsyncMock(return_value={"success": True})
    tool_ctx.github_client.swap_labels = AsyncMock(return_value={"success": True})

    result = json.loads(
        await release_issue(
            "https://github.com/owner/repo/issues/42",
            label="autoskillit:in-progress",
            target_branch="integration-branch",
        )
    )
    assert result["success"] is True
    assert result["staged"] is True
    assert result["staged_label"] == "staged"
