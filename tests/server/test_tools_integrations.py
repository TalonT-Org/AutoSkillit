"""Tests for the report_bug and fetch_github_issue MCP tool handlers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from autoskillit.core import SkillResult
from autoskillit.core.types import RetryReason
from autoskillit.pipeline.gate import UNGATED_TOOLS
from autoskillit.server.tools_integrations import (
    _FINGERPRINT_END,
    _FINGERPRINT_START,
    _parse_fingerprint,
    fetch_github_issue,
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
# report_bug gate tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_report_bug_gate_closed(tool_ctx):
    tool_ctx.gate.disable()
    result = json.loads(await report_bug("some error", "/tmp"))
    assert result["success"] is False
    assert "not enabled" in result["result"].lower() or "gate" in result["result"].lower()


@pytest.mark.anyio
async def test_report_bug_no_executor(tool_ctx):
    tool_ctx.executor = None
    result = json.loads(await report_bug("error ctx", "/tmp"))
    assert result["success"] is False
    assert "executor" in result["error"].lower()


# ---------------------------------------------------------------------------
# Helpers: build a mock SkillResult
# ---------------------------------------------------------------------------


def _skill_ok(report_text: str = "## Bug Report\ndetails") -> SkillResult:
    return SkillResult(
        success=True,
        result=report_text,
        session_id="sid",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )


def _skill_fail() -> SkillResult:
    return SkillResult(
        success=False,
        result="",
        session_id="",
        subtype="error",
        is_error=True,
        exit_code=1,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="something went wrong",
    )


# ---------------------------------------------------------------------------
# Blocking mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_report_bug_blocking_success(tool_ctx, tmp_path):
    """Blocking mode awaits the session and returns status=complete."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = False

    mock_executor = AsyncMock()
    mock_executor.run.return_value = _skill_ok("## Report\nroot cause found")
    tool_ctx.executor = mock_executor

    result = json.loads(await report_bug("KeyError in foo", str(tmp_path), severity="blocking"))

    assert result["success"] is True
    assert result["status"] == "complete"
    assert "report" in result
    assert "report_path" in result
    mock_executor.run.assert_awaited_once()


@pytest.mark.anyio
async def test_report_bug_blocking_failure_propagated(tool_ctx, tmp_path):
    """If the headless session fails, status=failed is returned."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = False

    mock_executor = AsyncMock()
    mock_executor.run.return_value = _skill_fail()
    tool_ctx.executor = mock_executor

    result = json.loads(await report_bug("crash here", str(tmp_path), severity="blocking"))

    assert result["success"] is False
    assert result["status"] == "failed"


@pytest.mark.anyio
async def test_report_bug_blocking_writes_report_file(tool_ctx, tmp_path):
    """The report text must be written to the resolved report_path."""
    report_dir = tmp_path / "rpts"
    tool_ctx.config.report_bug.report_dir = str(report_dir)
    tool_ctx.config.report_bug.github_filing = False

    mock_executor = AsyncMock()
    mock_executor.run.return_value = _skill_ok("# Bug Report\nfoo bar")
    tool_ctx.executor = mock_executor

    result = json.loads(await report_bug("err", str(tmp_path), severity="blocking"))

    report_path = Path(result["report_path"])
    assert report_path.exists()
    assert "Bug Report" in report_path.read_text()


# ---------------------------------------------------------------------------
# Non-blocking mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_report_bug_non_blocking_returns_immediately(tool_ctx, tmp_path):
    """Non-blocking mode must return dispatched before the session completes."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = False

    ready = anyio.Event()

    async def slow_run(*args, **kwargs):
        await ready.wait()  # blocks until test signals
        return _skill_ok()

    mock_executor = MagicMock()
    mock_executor.run = slow_run
    tool_ctx.executor = mock_executor

    result = json.loads(await report_bug("error ctx", str(tmp_path), severity="non_blocking"))

    assert result["success"] is True
    assert result["status"] == "dispatched"
    assert "report_path" in result

    # Let the background task finish cleanly.
    ready.set()
    await anyio.sleep(0)


@pytest.mark.anyio
async def test_report_bug_non_blocking_default_severity(tool_ctx, tmp_path):
    """The default severity is non_blocking."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = False

    mock_executor = AsyncMock()
    mock_executor.run.return_value = _skill_ok()
    tool_ctx.executor = mock_executor

    result = json.loads(await report_bug("err", str(tmp_path)))
    assert result["status"] == "dispatched"


# ---------------------------------------------------------------------------
# GitHub filing — blocking mode (easier to assert synchronously)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_report_bug_creates_github_issue_on_no_duplicate(tool_ctx, tmp_path):
    """When no matching issue is found, create_issue is called."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = True
    tool_ctx.config.github.default_repo = "owner/repo"

    report_with_fp = (
        f"{_FINGERPRINT_START}\n"
        "KeyError in recipe/validator.py: missing ref\n"
        f"{_FINGERPRINT_END}\n"
        "## Bug Report\ndetails"
    )
    mock_executor = AsyncMock()
    mock_executor.run.return_value = _skill_ok(report_with_fp)
    tool_ctx.executor = mock_executor

    mock_gh = AsyncMock()
    mock_gh.has_token = True
    mock_gh.search_issues.return_value = {"success": True, "total_count": 0, "items": []}
    mock_gh.create_issue.return_value = {
        "success": True,
        "issue_number": 99,
        "url": "https://github.com/owner/repo/issues/99",
    }
    tool_ctx.github_client = mock_gh

    result = json.loads(await report_bug("KeyError crash", str(tmp_path), severity="blocking"))

    assert result["success"] is True
    mock_gh.search_issues.assert_awaited_once()
    mock_gh.create_issue.assert_awaited_once()
    assert result["github"]["duplicate"] is False
    assert result["github"]["issue_url"] == "https://github.com/owner/repo/issues/99"


@pytest.mark.anyio
async def test_report_bug_comments_on_duplicate_issue(tool_ctx, tmp_path):
    """When a matching issue exists and error_context is new, add_comment is called."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = True
    tool_ctx.config.github.default_repo = "owner/repo"

    mock_executor = AsyncMock()
    mock_executor.run.return_value = _skill_ok(
        "## Report\n" + _FINGERPRINT_START + "\nfp\n" + _FINGERPRINT_END
    )
    tool_ctx.executor = mock_executor

    mock_gh = AsyncMock()
    mock_gh.has_token = True
    mock_gh.search_issues.return_value = {
        "success": True,
        "total_count": 1,
        "items": [
            {
                "number": 7,
                "title": "fp",
                "html_url": "https://github.com/owner/repo/issues/7",
                "body": "Original body — no error_context here",
                "state": "open",
            }
        ],
    }
    mock_gh.add_comment.return_value = {"success": True, "comment_id": 55, "url": "u"}
    tool_ctx.github_client = mock_gh

    result = json.loads(
        await report_bug("brand new error text", str(tmp_path), severity="blocking")
    )

    assert result["success"] is True
    mock_gh.add_comment.assert_awaited_once()
    assert result["github"]["duplicate"] is True
    assert result["github"]["comment_added"] is True


@pytest.mark.anyio
async def test_report_bug_skips_comment_if_already_present(tool_ctx, tmp_path):
    """If error_context is already in the issue body, no comment is posted."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = True
    tool_ctx.config.github.default_repo = "owner/repo"

    error_ctx = "exact error text already filed"
    mock_executor = AsyncMock()
    mock_executor.run.return_value = _skill_ok()
    tool_ctx.executor = mock_executor

    mock_gh = AsyncMock()
    mock_gh.has_token = True
    mock_gh.search_issues.return_value = {
        "success": True,
        "total_count": 1,
        "items": [
            {
                "number": 3,
                "title": "fp",
                "html_url": "https://github.com/owner/repo/issues/3",
                "body": f"body contains {error_ctx} already",
                "state": "open",
            }
        ],
    }
    tool_ctx.github_client = mock_gh

    result = json.loads(await report_bug(error_ctx, str(tmp_path), severity="blocking"))

    mock_gh.add_comment.assert_not_awaited()
    assert result["github"]["comment_added"] is False


@pytest.mark.anyio
async def test_report_bug_skips_github_if_no_token(tool_ctx, tmp_path):
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = True

    mock_executor = AsyncMock()
    mock_executor.run.return_value = _skill_ok()
    tool_ctx.executor = mock_executor

    mock_gh = AsyncMock()
    mock_gh.has_token = False
    tool_ctx.github_client = mock_gh

    result = json.loads(await report_bug("err", str(tmp_path), severity="blocking"))

    assert result["success"] is True
    mock_gh.search_issues.assert_not_awaited()
    assert result["github"]["skipped"] is True
    assert result["github"]["reason"] == "no_token"


@pytest.mark.anyio
async def test_report_bug_skips_github_if_no_default_repo(tool_ctx, tmp_path):
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = True
    tool_ctx.config.github.default_repo = None

    mock_executor = AsyncMock()
    mock_executor.run.return_value = _skill_ok()
    tool_ctx.executor = mock_executor

    mock_gh = AsyncMock()
    mock_gh.has_token = True
    tool_ctx.github_client = mock_gh

    result = json.loads(await report_bug("err", str(tmp_path), severity="blocking"))

    mock_gh.search_issues.assert_not_awaited()
    assert result["github"]["skipped"] is True


@pytest.mark.anyio
async def test_report_bug_github_filing_disabled(tool_ctx, tmp_path):
    """github_filing=false must skip all GitHub calls."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = False
    tool_ctx.config.github.default_repo = "owner/repo"

    mock_executor = AsyncMock()
    mock_executor.run.return_value = _skill_ok()
    tool_ctx.executor = mock_executor

    mock_gh = AsyncMock()
    mock_gh.has_token = True
    tool_ctx.github_client = mock_gh

    result = json.loads(await report_bug("err", str(tmp_path), severity="blocking"))

    mock_gh.search_issues.assert_not_awaited()
    assert result["github"] == {}


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


def test_report_bug_config_defaults():
    from autoskillit.config import AutomationConfig

    cfg = AutomationConfig()
    assert cfg.report_bug.timeout == 600
    assert cfg.report_bug.model is None
    assert cfg.report_bug.report_dir is None
    assert cfg.report_bug.github_filing is True
    assert "autoreported" in cfg.report_bug.github_labels
    assert "bug" in cfg.report_bug.github_labels





@pytest.mark.anyio
async def test_fetch_github_issue_no_client(tool_ctx):
    tool_ctx.github_client = None
    result = json.loads(await fetch_github_issue("owner/repo#1"))
    assert result["success"] is False
    assert "error" in result


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
async def test_fetch_github_issue_bare_number_with_default_repo(tool_ctx):
    tool_ctx.config.github.default_repo = "owner/repo"
    mock_client = AsyncMock()
    mock_client.fetch_issue.return_value = {
        "success": True,
        "issue_number": 42,
        "title": "T",
        "url": "u",
        "state": "open",
        "labels": [],
        "content": "# T",
    }
    tool_ctx.github_client = mock_client
    result = json.loads(await fetch_github_issue("42"))
    assert result["success"] is True
    mock_client.fetch_issue.assert_called_once_with("owner/repo#42", include_comments=True)


@pytest.mark.anyio
async def test_fetch_github_issue_bare_number_no_default_repo(tool_ctx):
    tool_ctx.config.github.default_repo = None
    tool_ctx.github_client = AsyncMock()
    result = json.loads(await fetch_github_issue("42"))
    assert result["success"] is False
    assert "default_repo" in result["error"]


@pytest.mark.anyio
async def test_fetch_github_issue_client_error_propagated(tool_ctx):
    mock_client = AsyncMock()
    mock_client.fetch_issue.return_value = {"success": False, "error": "Not Found"}
    tool_ctx.github_client = mock_client
    result = json.loads(await fetch_github_issue("owner/repo#404"))
    assert result["success"] is False


def test_fetch_github_issue_in_ungated_tools():
    assert "fetch_github_issue" in UNGATED_TOOLS


def test_github_config_defaults():
    from autoskillit.config import AutomationConfig

    config = AutomationConfig()
    assert config.github.token is None
    assert config.github.default_repo is None
