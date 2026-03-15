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
    _format_diagnostics_section,
    _parse_enrich_result,
    _parse_fingerprint,
    _parse_prepare_result,
    _read_session_diagnostics,
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
        assert captured == {
            "tool": "claim_issue",
            "issue_url": "https://github.com/owner/repo/issues/1",
        }


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


# ---------------------------------------------------------------------------
# _read_session_diagnostics unit tests
# ---------------------------------------------------------------------------


def test_read_session_diagnostics_returns_none_for_empty_session_id(tmp_path):
    """Empty session_id → None, no filesystem access."""
    result = _read_session_diagnostics("", str(tmp_path))
    assert result is None


def test_read_session_diagnostics_returns_none_for_no_session_prefix(tmp_path):
    """no_session_* session IDs → None (not meaningful diagnostics)."""
    assert _read_session_diagnostics("no_session_2026-01-01T00-00-00", str(tmp_path)) is None


def test_read_session_diagnostics_returns_none_for_crashed_prefix(tmp_path):
    """crashed_* session IDs → None (not meaningful diagnostics)."""
    assert _read_session_diagnostics("crashed_12345_2026-01-01T00-00-00", str(tmp_path)) is None


def test_read_session_diagnostics_returns_none_for_path_traversal_session_id(tmp_path):
    """Path-traversal session IDs blocked by _SAFE_SESSION_ID_RE → None."""
    assert _read_session_diagnostics("../../../etc/passwd", str(tmp_path)) is None
    assert _read_session_diagnostics("..%2F..%2Fetc%2Fpasswd", str(tmp_path)) is None
    assert _read_session_diagnostics("abc/../../etc", str(tmp_path)) is None


def test_read_session_diagnostics_returns_none_when_directory_missing(tmp_path):
    """Valid session_id but no directory on disk → None."""
    result = _read_session_diagnostics("abc-123", str(tmp_path))
    assert result is None


def test_read_session_diagnostics_reads_summary_json(tmp_path):
    """Reads and returns summary.json contents."""
    session_id = "test-session-abc"
    session_dir = tmp_path / "sessions" / session_id
    session_dir.mkdir(parents=True)
    summary = {
        "session_id": session_id,
        "duration_seconds": 42.0,
        "peak_rss_kb": 1024,
        "peak_oom_score": 5,
        "anomaly_count": 0,
        "termination_reason": "NATURAL_EXIT",
        "exit_code": 0,
        "claude_code_log": "/path/to/log.jsonl",
    }
    (session_dir / "summary.json").write_text(json.dumps(summary))

    result = _read_session_diagnostics(session_id, str(tmp_path))
    assert result is not None
    assert result["summary"]["duration_seconds"] == 42.0
    assert result["session_id"] == session_id
    assert result["session_dir"] == str(session_dir)


def test_read_session_diagnostics_reads_anomalies_jsonl(tmp_path):
    """Reads all records from anomalies.jsonl."""
    session_id = "test-session-def"
    session_dir = tmp_path / "sessions" / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(json.dumps({"session_id": session_id}))
    anomalies = [
        {"kind": "oom_spike", "severity": "warning", "detail": {"delta": 250}},
        {"kind": "rss_growth", "severity": "warning", "detail": {"ratio": 2.5}},
    ]
    (session_dir / "anomalies.jsonl").write_text("\n".join(json.dumps(a) for a in anomalies))

    result = _read_session_diagnostics(session_id, str(tmp_path))
    assert len(result["anomalies"]) == 2
    assert result["anomalies"][0]["kind"] == "oom_spike"


def test_read_session_diagnostics_reads_proc_trace_tail_10(tmp_path):
    """Reads only the last 10 lines of proc_trace.jsonl."""
    session_id = "test-session-ghi"
    session_dir = tmp_path / "sessions" / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(json.dumps({"session_id": session_id}))
    snapshots = [{"seq": i, "vm_rss_kb": i * 100} for i in range(15)]
    (session_dir / "proc_trace.jsonl").write_text("\n".join(json.dumps(s) for s in snapshots))

    result = _read_session_diagnostics(session_id, str(tmp_path))
    assert len(result["proc_trace_tail"]) == 10
    assert result["proc_trace_tail"][0]["seq"] == 5  # starts from seq=5 (last 10 of 15)


def test_read_session_diagnostics_handles_missing_optional_files(tmp_path):
    """Returns empty lists when anomalies.jsonl and proc_trace.jsonl are absent."""
    session_id = "test-session-jkl"
    session_dir = tmp_path / "sessions" / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(json.dumps({"session_id": session_id}))

    result = _read_session_diagnostics(session_id, str(tmp_path))
    assert result["anomalies"] == []
    assert result["proc_trace_tail"] == []


# ---------------------------------------------------------------------------
# _format_diagnostics_section unit tests
# ---------------------------------------------------------------------------


def test_format_diagnostics_section_full_includes_metrics_table():
    """Full format includes the metrics table."""
    diag = {
        "session_id": "abc-123",
        "session_dir": "/logs/sessions/abc-123",
        "summary": {
            "session_id": "abc-123",
            "duration_seconds": 30.5,
            "peak_rss_kb": 2048,
            "peak_oom_score": 10,
            "anomaly_count": 1,
            "termination_reason": "NATURAL_EXIT",
            "exit_code": 0,
            "claude_code_log": "/logs/claude.jsonl",
        },
        "anomalies": [{"kind": "oom_spike", "severity": "warning", "detail": {"delta": 210}}],
        "proc_trace_tail": [],
    }
    output = _format_diagnostics_section(diag, condensed=False)
    assert "## Session Diagnostics" in output
    assert "Session ID" in output
    assert "abc-123" in output
    assert "30.5s" in output
    assert "2048 KB" in output


def test_format_diagnostics_section_full_includes_anomalies_details_block():
    """Full format includes <details> block when anomalies present."""
    diag = {
        "session_id": "abc-123",
        "session_dir": "/logs/sessions/abc-123",
        "summary": {"session_id": "abc-123", "anomaly_count": 1},
        "anomalies": [{"kind": "oom_spike", "severity": "warning", "detail": {}}],
        "proc_trace_tail": [],
    }
    output = _format_diagnostics_section(diag, condensed=False)
    assert "<details>" in output
    assert "Anomalies (1)" in output


def test_format_diagnostics_section_full_includes_proc_trace_block():
    """Full format includes <details> block for proc trace when snapshots present."""
    diag = {
        "session_id": "abc-123",
        "session_dir": "/logs/sessions/abc-123",
        "summary": {"session_id": "abc-123"},
        "anomalies": [],
        "proc_trace_tail": [{"seq": 0, "vm_rss_kb": 100}],
    }
    output = _format_diagnostics_section(diag, condensed=False)
    assert "Process Trace" in output
    assert "```json" in output


def test_format_diagnostics_section_full_omits_blocks_when_empty():
    """Full format omits <details> blocks when no anomalies and no proc trace."""
    diag = {
        "session_id": "abc-123",
        "session_dir": "/logs/sessions/abc-123",
        "summary": {"session_id": "abc-123", "anomaly_count": 0},
        "anomalies": [],
        "proc_trace_tail": [],
    }
    output = _format_diagnostics_section(diag, condensed=False)
    assert "<details>" not in output


def test_format_diagnostics_section_full_includes_local_paths():
    """Full format includes local path links."""
    diag = {
        "session_id": "abc-123",
        "session_dir": "/logs/sessions/abc-123",
        "summary": {"session_id": "abc-123", "claude_code_log": "/claude/log.jsonl"},
        "anomalies": [],
        "proc_trace_tail": [],
    }
    output = _format_diagnostics_section(diag, condensed=False)
    assert "/logs/sessions/abc-123" in output
    assert "/claude/log.jsonl" in output


def test_format_diagnostics_section_condensed_has_metrics_only():
    """Condensed format has metrics table but no <details> blocks or paths."""
    diag = {
        "session_id": "abc-123",
        "session_dir": "/logs/sessions/abc-123",
        "summary": {
            "session_id": "abc-123",
            "duration_seconds": 5.0,
            "peak_rss_kb": 512,
            "peak_oom_score": 2,
            "anomaly_count": 0,
            "termination_reason": "NATURAL_EXIT",
            "exit_code": 0,
            "claude_code_log": "/claude/log.jsonl",
        },
        "anomalies": [{"kind": "oom_spike", "severity": "warning", "detail": {}}],
        "proc_trace_tail": [{"seq": 0}],
    }
    output = _format_diagnostics_section(diag, condensed=True)
    assert "## Session Diagnostics" in output
    assert "<details>" not in output
    assert "Local paths" not in output


# ---------------------------------------------------------------------------
# Integration test helpers
# ---------------------------------------------------------------------------


def _make_mock_executor(success: bool, result: str, session_id: str) -> MagicMock:
    """Return a mock HeadlessExecutor whose run() returns a SkillResult."""
    skill_result = SkillResult(
        success=success,
        result=result,
        session_id=session_id,
        subtype="success" if success else "error",
        is_error=not success,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )
    executor = MagicMock()
    executor.run = AsyncMock(return_value=skill_result)
    return executor


def _make_mock_github(search_total: int, existing_body: str = "") -> MagicMock:
    """Return a mock GitHubFetcher for issue search + create/comment."""
    client = MagicMock()
    client.has_token = True
    items = (
        [
            {
                "number": 1,
                "html_url": "https://github.com/o/r/issues/1",
                "body": existing_body,
            }
        ]
        if search_total > 0
        else []
    )
    client.search_issues = AsyncMock(
        return_value={"success": True, "total_count": search_total, "items": items}
    )
    client.create_issue = AsyncMock(
        return_value={"success": True, "url": "https://github.com/o/r/issues/99"}
    )
    client.add_comment = AsyncMock(return_value={"success": True})
    return client


def _make_session_dir(
    tmp_path: Path,
    session_id: str,
    summary_extra: dict | None = None,
    anomalies: list | None = None,
    proc_trace: list | None = None,
) -> Path:
    """Helper: create a fake session log directory."""
    session_dir = tmp_path / "session_logs" / "sessions" / session_id
    session_dir.mkdir(parents=True)
    summary: dict = {
        "session_id": session_id,
        "duration_seconds": 10.0,
        "peak_rss_kb": 1024,
        "peak_oom_score": 5,
        "anomaly_count": len(anomalies or []),
        "termination_reason": "NATURAL_EXIT",
        "exit_code": 0,
        "claude_code_log": None,
    }
    summary.update(summary_extra or {})
    (session_dir / "summary.json").write_text(json.dumps(summary))
    if anomalies:
        (session_dir / "anomalies.jsonl").write_text("\n".join(json.dumps(a) for a in anomalies))
    if proc_trace:
        (session_dir / "proc_trace.jsonl").write_text("\n".join(json.dumps(s) for s in proc_trace))
    return session_dir


# ---------------------------------------------------------------------------
# Integration tests: full report_bug flow with diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_report_bug_includes_diagnostics_in_new_issue_body(tool_ctx, tmp_path):
    """New issue body includes the Session Diagnostics section when diagnostics are available."""
    session_id = "diag-session-001"
    _make_session_dir(tmp_path, session_id)

    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = True
    tool_ctx.config.github.default_repo = "owner/repo"
    tool_ctx.config.linux_tracing.log_dir = str(tmp_path / "session_logs")

    tool_ctx.executor = _make_mock_executor(
        success=True,
        result="---bug-fingerprint---\nfp-001\n---/bug-fingerprint---\nReport text.",
        session_id=session_id,
    )
    github_mock = _make_mock_github(search_total=0)
    tool_ctx.github_client = github_mock

    result = json.loads(
        await report_bug(error_context="Test error", cwd=str(tmp_path), severity="blocking")
    )

    assert result["success"] is True
    _args = github_mock.create_issue.call_args
    call_body = _args.kwargs.get("body", _args.args[3])
    assert "## Session Diagnostics" in call_body
    assert session_id in call_body


@pytest.mark.anyio
async def test_report_bug_includes_condensed_diagnostics_in_duplicate_comment(tool_ctx, tmp_path):
    """Duplicate comment includes condensed metrics but no <details> blocks."""
    session_id = "diag-session-002"
    _make_session_dir(
        tmp_path,
        session_id,
        anomalies=[{"kind": "oom_spike", "severity": "warning", "detail": {}}],
    )

    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = True
    tool_ctx.config.github.default_repo = "owner/repo"
    tool_ctx.config.linux_tracing.log_dir = str(tmp_path / "session_logs")

    tool_ctx.executor = _make_mock_executor(
        success=True,
        result="---bug-fingerprint---\nfp-002\n---/bug-fingerprint---\nReport text.",
        session_id=session_id,
    )
    github_mock = _make_mock_github(search_total=1, existing_body="different error")
    tool_ctx.github_client = github_mock

    await report_bug(error_context="Test error", cwd=str(tmp_path), severity="blocking")

    _args = github_mock.add_comment.call_args
    comment_body = _args.kwargs.get("body", _args.args[3])
    assert "Session Diagnostics" in comment_body
    assert "<details>" not in comment_body  # condensed — no details blocks


@pytest.mark.anyio
async def test_report_bug_proceeds_without_diagnostics_when_session_dir_missing(
    tool_ctx, tmp_path
):
    """GitHub issue is still filed even when no session diagnostics directory exists."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = True
    tool_ctx.config.github.default_repo = "owner/repo"
    tool_ctx.config.linux_tracing.log_dir = str(tmp_path / "session_logs")

    tool_ctx.executor = _make_mock_executor(
        success=True,
        result="---bug-fingerprint---\nfp-003\n---/bug-fingerprint---\nReport text.",
        session_id="nonexistent-session-id",  # no directory on disk
    )
    github_mock = _make_mock_github(search_total=0)
    tool_ctx.github_client = github_mock

    result = json.loads(
        await report_bug(error_context="Test error", cwd=str(tmp_path), severity="blocking")
    )

    assert result["success"] is True
    assert github_mock.create_issue.called
    _args = github_mock.create_issue.call_args
    call_body = _args.kwargs.get("body", _args.args[3])
    assert "## Session Diagnostics" not in call_body  # graceful skip


@pytest.mark.anyio
async def test_report_bug_skips_diagnostics_for_fallback_session_id(tool_ctx, tmp_path):
    """Fallback session IDs (no_session_*) never trigger diagnostics read."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = True
    tool_ctx.config.github.default_repo = "owner/repo"
    tool_ctx.config.linux_tracing.log_dir = str(tmp_path / "session_logs")

    tool_ctx.executor = _make_mock_executor(
        success=True,
        result="---bug-fingerprint---\nfp-004\n---/bug-fingerprint---\nReport.",
        session_id="no_session_2026-01-01T00-00-00",
    )
    github_mock = _make_mock_github(search_total=0)
    tool_ctx.github_client = github_mock

    result = json.loads(
        await report_bug(error_context="Test error", cwd=str(tmp_path), severity="blocking")
    )

    assert result["success"] is True
    _args = github_mock.create_issue.call_args
    call_body = _args.kwargs.get("body", _args.args[3])
    assert "## Session Diagnostics" not in call_body
