"""Tests for server/tools_github.py — fetch_github_issue and get_issue_title."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import SkillResult
from autoskillit.core.types import RetryReason
from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS, DefaultGateState
from autoskillit.server.tools_github import (
    _FINGERPRINT_END,
    _FINGERPRINT_START,
    _parse_fingerprint,
    fetch_github_issue,
    get_issue_title,
    report_bug,
)

# ---------------------------------------------------------------------------
# _parse_fingerprint unit tests
# ---------------------------------------------------------------------------


def test_parse_fingerprint_present():
    report = (
        "Some preamble\n"
        f"{_FINGERPRINT_START}\n"
        "KeyError in recipe/validator.py: missing ingredient ref\n"
        f"{_FINGERPRINT_END}\n"
        "Report written to /tmp/report.md"
    )
    assert _parse_fingerprint(report) == "KeyError in recipe/validator.py: missing ingredient ref"


def test_parse_fingerprint_missing_returns_none():
    assert _parse_fingerprint("No fingerprint block here") is None


def test_parse_fingerprint_empty_block_returns_none():
    report = f"{_FINGERPRINT_START}\n{_FINGERPRINT_END}\n"
    assert _parse_fingerprint(report) is None


def test_parse_fingerprint_first_nonempty_line():
    """Only the first non-empty line inside the block is returned."""
    report = (
        f"{_FINGERPRINT_START}\n"
        "\n"
        "  TypeError in execution/headless.py: runner=None  \n"
        "extra line\n"
        f"{_FINGERPRINT_END}\n"
    )
    assert _parse_fingerprint(report) == "TypeError in execution/headless.py: runner=None"


# ---------------------------------------------------------------------------
# fetch_github_issue
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_github_issue_gate_closed(tool_ctx) -> None:
    """Gate disabled → gate error JSON."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await fetch_github_issue("owner/repo#42"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_fetch_github_issue_no_client(tool_ctx) -> None:
    """github_client=None → {"success": False, "error": "GitHub client not configured"}."""
    tool_ctx.github_client = None
    result = json.loads(await fetch_github_issue("owner/repo#42"))
    assert result["success"] is False
    assert "GitHub client not configured" in result["error"]


@pytest.mark.anyio
async def test_fetch_github_issue_success(tool_ctx) -> None:
    """client.fetch_issue returns data → JSON with that data."""
    issue_data = {
        "success": True,
        "issue_number": 42,
        "title": "Test issue",
        "url": "https://github.com/owner/repo/issues/42",
        "state": "open",
        "labels": [],
        "content": "## Body\nSome content.",
    }
    tool_ctx.github_client = AsyncMock()
    tool_ctx.github_client.fetch_issue = AsyncMock(return_value=issue_data)

    result = json.loads(await fetch_github_issue("https://github.com/owner/repo/issues/42"))
    assert result["success"] is True
    assert result["issue_number"] == 42


@pytest.mark.anyio
async def test_fetch_github_issue_bare_number_no_default_repo(tool_ctx) -> None:
    """issue_url='42', no default_repo → error response."""
    tool_ctx.github_client = AsyncMock()
    tool_ctx.config.github.default_repo = ""

    result = json.loads(await fetch_github_issue("42"))
    assert result["success"] is False
    assert "default_repo" in result["error"] or "bare issue number" in result["error"].lower()


@pytest.mark.anyio
async def test_fetch_github_issue_bare_number_with_default_repo(
    tool_ctx,
) -> None:
    """issue_url='42', default_repo='owner/repo' → resolves to 'owner/repo#42'."""
    tool_ctx.config.github.default_repo = "owner/repo"
    issue_data = {
        "success": True,
        "issue_number": 42,
        "title": "From bare number",
        "url": "https://github.com/owner/repo/issues/42",
        "state": "open",
        "labels": [],
        "content": "content",
    }
    tool_ctx.github_client = AsyncMock()
    tool_ctx.github_client.fetch_issue = AsyncMock(return_value=issue_data)

    result = json.loads(await fetch_github_issue("42"))
    assert result["success"] is True
    # Verify fetch_issue was called with the resolved ref
    call_args = tool_ctx.github_client.fetch_issue.call_args
    assert "owner/repo#42" in call_args.args[0]


@pytest.mark.anyio
async def test_fetch_github_issue_delegates_to_client(tool_ctx):
    mock_client = AsyncMock()
    mock_client.fetch_issue.return_value = {
        "success": True,
        "issue_number": 1,
        "title": "T",
        "url": "u",
        "state": "open",
        "labels": [],
        "content": "# T",
    }
    tool_ctx.github_client = mock_client
    result = json.loads(await fetch_github_issue("owner/repo#1"))
    assert result["success"] is True
    mock_client.fetch_issue.assert_called_once_with("owner/repo#1", include_comments=True)


@pytest.mark.anyio
async def test_fetch_github_issue_client_error_propagated(tool_ctx):
    mock_client = AsyncMock()
    mock_client.fetch_issue.return_value = {"success": False, "error": "Not Found"}
    tool_ctx.github_client = mock_client
    result = json.loads(await fetch_github_issue("owner/repo#404"))
    assert result["success"] is False


def test_fetch_github_issue_in_gated_tools():
    assert "fetch_github_issue" in GATED_TOOLS
    assert "fetch_github_issue" not in UNGATED_TOOLS


# ---------------------------------------------------------------------------
# get_issue_title
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_issue_title_gate_closed(tool_ctx) -> None:
    """Gate disabled → gate error JSON."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await get_issue_title("owner/repo#42"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_get_issue_title_no_client(tool_ctx) -> None:
    """github_client=None → error response."""
    tool_ctx.github_client = None
    result = json.loads(await get_issue_title("owner/repo#42"))
    assert result["success"] is False


@pytest.mark.anyio
async def test_get_issue_title_success(tool_ctx) -> None:
    """client.fetch_title returns data → JSON with title and slug."""
    title_data = {
        "success": True,
        "number": 42,
        "title": "Fix the bug",
        "slug": "fix-the-bug",
    }
    tool_ctx.github_client = AsyncMock()
    tool_ctx.github_client.fetch_title = AsyncMock(return_value=title_data)

    result = json.loads(await get_issue_title("owner/repo#42"))
    assert result["success"] is True
    assert result["title"] == "Fix the bug"


class TestGetIssueTitleTool:
    @pytest.mark.anyio
    async def test_get_issue_title_success(self, tool_ctx):
        """Delegates to github_client.fetch_title; returns JSON result."""
        mock_client = AsyncMock()
        mock_client.fetch_title.return_value = {
            "success": True,
            "number": 42,
            "title": "Fix merge conflict triage",
            "slug": "fix-merge-conflict-triage",
        }
        tool_ctx.github_client = mock_client
        result = json.loads(await get_issue_title("https://github.com/owner/repo/issues/42"))
        assert result["success"] is True
        assert result["number"] == 42
        assert result["title"] == "Fix merge conflict triage"
        assert result["slug"] == "fix-merge-conflict-triage"
        mock_client.fetch_title.assert_called_once_with("https://github.com/owner/repo/issues/42")

    @pytest.mark.anyio
    async def test_get_issue_title_client_error_propagated(self, tool_ctx):
        """Propagates {success: False, error: ...} from fetch_title."""
        mock_client = AsyncMock()
        mock_client.fetch_title.return_value = {"success": False, "error": "Not Found"}
        tool_ctx.github_client = mock_client
        result = json.loads(await get_issue_title("owner/repo#404"))
        assert result["success"] is False

    def test_get_issue_title_is_gated(self):
        """'get_issue_title' in GATED_TOOLS."""
        assert "get_issue_title" in GATED_TOOLS
        assert "get_issue_title" not in UNGATED_TOOLS


# ---------------------------------------------------------------------------
# report_bug config defaults
# ---------------------------------------------------------------------------


def test_report_bug_config_defaults():
    cfg = AutomationConfig()
    assert cfg.report_bug.timeout == 600
    assert cfg.report_bug.model is None
    assert cfg.report_bug.report_dir is None
    assert cfg.report_bug.github_filing is True
    assert "autoreported" in cfg.report_bug.github_labels
    assert "bug" in cfg.report_bug.github_labels


def test_github_config_defaults():
    config = AutomationConfig()
    assert config.github.token is None
    assert config.github.default_repo is None


# ---------------------------------------------------------------------------
# TestReportBugTool
# ---------------------------------------------------------------------------


class TestReportBugTool:
    @pytest.mark.anyio
    async def test_report_bug_failure_includes_session_id_and_stderr(self, tool_ctx, tmp_path):
        """Blocking failure response must include session_id and stderr for diagnosis."""
        tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
        tool_ctx.config.report_bug.github_filing = False

        mock_executor = AsyncMock()
        mock_executor.run.return_value = SkillResult(
            success=False,
            result="",
            session_id="fail-session-id",
            subtype="missing_completion_marker",
            is_error=True,
            exit_code=1,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="Claude crashed",
        )
        tool_ctx.executor = mock_executor

        result = json.loads(await report_bug("some error", str(tmp_path), severity="blocking"))

        assert result["success"] is False
        assert result["session_id"] == "fail-session-id"
        assert result["stderr"] == "Claude crashed"

    @pytest.mark.anyio
    async def test_report_bug_passes_expected_output_patterns_to_executor(
        self, tool_ctx, tmp_path
    ):
        """output_pattern_resolver is consulted and patterns are passed to executor.run()."""
        tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
        tool_ctx.config.report_bug.github_filing = False

        mock_executor = AsyncMock()
        mock_executor.run.return_value = SkillResult(
            success=True,
            result="## Report\nfindings",
            session_id="sid",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        tool_ctx.executor = mock_executor
        tool_ctx.output_pattern_resolver = lambda cmd: ["---bug-fingerprint---"]

        await report_bug("error ctx", str(tmp_path), severity="blocking")

        call_kwargs = mock_executor.run.call_args.kwargs
        assert call_kwargs.get("expected_output_patterns") == ["---bug-fingerprint---"]
