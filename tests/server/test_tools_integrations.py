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
    _ENRICH_RESULT_END,
    _ENRICH_RESULT_START,
    _FINGERPRINT_END,
    _FINGERPRINT_START,
    _PREPARE_RESULT_END,
    _PREPARE_RESULT_START,
    _extract_block,
    _parse_enrich_result,
    _parse_fingerprint,
    _parse_prepare_result,
    bulk_close_issues,
    claim_issue,
    enrich_issues,
    fetch_github_issue,
    get_issue_title,
    get_pr_reviews,
    prepare_issue,
    release_issue,
    report_bug,
)
from tests.conftest import _make_result

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
# _extract_block unit tests
# ---------------------------------------------------------------------------


def test_extract_block_returns_lines_within_delimiters():
    text = "preamble\n---start---\nline1\nline2\n---end---\npostamble"
    assert _extract_block(text, "---start---", "---end---") == ["line1", "line2"]


def test_extract_block_no_start_returns_empty():
    assert _extract_block("no delimiters here", "---start---", "---end---") == []


def test_extract_block_no_end_returns_empty():
    # end delimiter absent — no complete block
    text = "---start---\nline1\nline2"
    assert _extract_block(text, "---start---", "---end---") == []


def test_extract_block_empty_block_returns_empty_list():
    text = "---start---\n---end---"
    assert _extract_block(text, "---start---", "---end---") == []


def test_extract_block_preserves_whitespace_in_lines():
    text = "---start---\n  indented\n---end---"
    assert _extract_block(text, "---start---", "---end---") == ["  indented"]


# ---------------------------------------------------------------------------
# _parse_prepare_result unit tests
# ---------------------------------------------------------------------------


def test_parse_prepare_result_valid_json():
    payload = '{"success": true, "issue_url": "https://github.com/x/y/issues/1"}'
    text = f"{_PREPARE_RESULT_START}\n{payload}\n{_PREPARE_RESULT_END}"
    result = _parse_prepare_result(text)
    assert result == {"success": True, "issue_url": "https://github.com/x/y/issues/1"}


def test_parse_prepare_result_no_block():
    result = _parse_prepare_result("no block here")
    assert result == {"success": False, "error": "no result block found"}


def test_parse_prepare_result_invalid_json():
    text = f"{_PREPARE_RESULT_START}\nnot-json\n{_PREPARE_RESULT_END}"
    result = _parse_prepare_result(text)
    assert result == {"success": False, "error": "result block contained invalid JSON"}


# ---------------------------------------------------------------------------
# _parse_enrich_result unit tests
# ---------------------------------------------------------------------------


def test_parse_enrich_result_valid_json():
    payload = '{"enriched": [42], "skipped_already_enriched": []}'
    text = f"{_ENRICH_RESULT_START}\n{payload}\n{_ENRICH_RESULT_END}"
    result = _parse_enrich_result(text)
    assert result == {"enriched": [42], "skipped_already_enriched": []}


def test_parse_enrich_result_no_block():
    result = _parse_enrich_result("no block here")
    assert result == {"success": False, "error": "no result block found"}


def test_parse_enrich_result_invalid_json():
    text = f"{_ENRICH_RESULT_START}\nnot-json\n{_ENRICH_RESULT_END}"
    result = _parse_enrich_result(text)
    assert result == {"success": False, "error": "result block contained invalid JSON"}


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


# ---------------------------------------------------------------------------
# get_issue_title tool tests
# ---------------------------------------------------------------------------


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
    async def test_get_issue_title_no_github_client(self, tool_ctx):
        """Returns error JSON when github_client is None."""
        tool_ctx.github_client = None
        result = json.loads(await get_issue_title("https://github.com/owner/repo/issues/1"))
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.anyio
    async def test_get_issue_title_client_error_propagated(self, tool_ctx):
        """Propagates {success: False, error: ...} from fetch_title."""
        mock_client = AsyncMock()
        mock_client.fetch_title.return_value = {"success": False, "error": "Not Found"}
        tool_ctx.github_client = mock_client
        result = json.loads(await get_issue_title("owner/repo#404"))
        assert result["success"] is False

    def test_get_issue_title_is_ungated(self):
        """'get_issue_title' in UNGATED_TOOLS."""
        from autoskillit.pipeline.gate import GATED_TOOLS

        assert "get_issue_title" in UNGATED_TOOLS
        assert "get_issue_title" not in GATED_TOOLS


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
        """claim_issue must bind structlog context vars via bound_contextvars."""
        import contextlib

        import structlog

        captured = {}

        @contextlib.contextmanager
        def fake_bound_contextvars(**kwargs):
            captured.update(kwargs)
            yield

        monkeypatch.setattr(structlog.contextvars, "bound_contextvars", fake_bound_contextvars)

        tool_ctx.github_client = None  # triggers early return after bind

        await claim_issue(issue_url="https://github.com/owner/repo/issues/1")
        assert captured == {"tool": "claim_issue", "issue_url": "https://github.com/owner/repo/issues/1"}


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
        """release_issue must bind structlog context vars via bound_contextvars."""
        import contextlib

        import structlog

        captured = {}

        @contextlib.contextmanager
        def fake_bound_contextvars(**kwargs):
            captured.update(kwargs)
            yield

        monkeypatch.setattr(structlog.contextvars, "bound_contextvars", fake_bound_contextvars)

        tool_ctx.github_client = None  # triggers early return after bind

        await release_issue(issue_url="https://github.com/owner/repo/issues/1")
        assert captured == {"tool": "release_issue", "issue_url": "https://github.com/owner/repo/issues/1"}


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
    async def test_comment_flag_included_when_provided(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, "", ""))
        await bulk_close_issues([7], "Closed by pipeline.", ".")
        call_cmd = tool_ctx.runner.call_args_list[-1][0]
        assert "--comment" in call_cmd

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx):
        tool_ctx.gate.disable()
        result = json.loads(await bulk_close_issues([1], "", "."))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"
