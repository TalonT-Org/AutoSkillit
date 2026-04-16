"""Tests for server/tools_github.py — fetch_github_issue and get_issue_title."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import SkillResult
from autoskillit.core.types import RetryReason
from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS, DefaultGateState
from autoskillit.server.helpers import _extract_block
from autoskillit.server.tools_github import (
    _FINGERPRINT_END,
    _FINGERPRINT_START,
    _parse_fingerprint,
    fetch_github_issue,
    get_issue_title,
    report_bug,
)
from autoskillit.server.tools_issue_lifecycle import (
    _ENRICH_RESULT_END,
    _ENRICH_RESULT_START,
    _PREPARE_RESULT_END,
    _PREPARE_RESULT_START,
    _parse_enrich_result,
    _parse_prepare_result,
)
from tests.server._helpers import _skill_fail, _skill_ok

pytestmark = [pytest.mark.layer("server")]

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
# Non-blocking outcome tests (supervised background task)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_report_bug_non_blocking_outcome_writes_report_file(tool_ctx, tmp_path):
    """After the background task completes, report_path must exist with content."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = False

    mock_executor = AsyncMock()
    mock_executor.run.return_value = _skill_ok("# Bug Report\nroot cause: missing guard")
    tool_ctx.executor = mock_executor

    result = json.loads(
        await report_bug("KeyError in foo", str(tmp_path), severity="non_blocking")
    )
    assert result["status"] == "dispatched"

    report_path = Path(result["report_path"])
    await tool_ctx.background.drain()

    assert report_path.exists(), "report_path must exist after background task completes"
    assert "Bug Report" in report_path.read_text()


@pytest.mark.anyio
async def test_report_bug_non_blocking_writes_status_file_on_success(tool_ctx, tmp_path):
    """A status.json file must be written with status='complete' after successful dispatch."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = False

    mock_executor = AsyncMock()
    mock_executor.run.return_value = _skill_ok("# Report\nfoo")
    tool_ctx.executor = mock_executor

    result = json.loads(await report_bug("err", str(tmp_path), severity="non_blocking"))
    report_path = Path(result["report_path"])

    # Before task runs: pending status file should exist
    status_path = report_path.with_suffix(".status.json")
    assert status_path.exists(), "status file must be written synchronously on dispatch"
    pending = json.loads(status_path.read_text())
    assert pending["status"] == "pending"

    # After task completes: status should update to complete
    await tool_ctx.background.drain()

    data = json.loads(status_path.read_text())
    assert data["status"] == "complete"
    assert "completed_at" in data


@pytest.mark.anyio
async def test_report_bug_non_blocking_writes_status_file_on_failure(tool_ctx, tmp_path):
    """When the headless session fails, status.json must reflect the failure."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = False

    mock_executor = AsyncMock()
    mock_executor.run.return_value = _skill_fail()
    tool_ctx.executor = mock_executor

    result = json.loads(await report_bug("crash here", str(tmp_path), severity="non_blocking"))
    status_path = Path(result["report_path"]).with_suffix(".status.json")

    await tool_ctx.background.drain()

    data = json.loads(status_path.read_text())
    assert data["status"] == "failed"
    assert "completed_at" in data


@pytest.mark.anyio
async def test_report_bug_non_blocking_executor_raises_is_observed(tool_ctx, tmp_path):
    """If executor.run() raises, the exception must be logged — not silently dropped."""

    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = False

    mock_executor = AsyncMock()
    mock_executor.run.side_effect = RuntimeError("executor exploded")
    tool_ctx.executor = mock_executor

    result = json.loads(await report_bug("error ctx", str(tmp_path), severity="non_blocking"))
    assert result["status"] == "dispatched"

    await tool_ctx.background.drain()

    # The exception must be captured and logged — not silently dropped
    status_path = Path(result["report_path"]).with_suffix(".status.json")
    assert status_path.exists(), "status file must exist even when executor raises"
    data = json.loads(status_path.read_text())
    assert data["status"] == "failed"
    assert "executor exploded" in data.get("error", "")


@pytest.mark.anyio
async def test_report_bug_no_pending_tasks_after_completion(tool_ctx, tmp_path):
    """After background task completes, no tasks remain in the supervisor's pending set."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = False

    mock_executor = AsyncMock()
    mock_executor.run.return_value = _skill_ok()
    tool_ctx.executor = mock_executor

    await report_bug("err", str(tmp_path), severity="non_blocking")

    await tool_ctx.background.drain()

    assert tool_ctx.background.pending_count == 0


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


@pytest.mark.anyio
async def test_report_bug_blocking_github_client_raises_does_not_propagate(tool_ctx, tmp_path):
    """If the GitHub client raises unexpectedly, the error must be captured in the github dict."""
    tool_ctx.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx.config.report_bug.github_filing = True
    tool_ctx.config.github.default_repo = "owner/repo"

    mock_executor = AsyncMock()
    mock_executor.run.return_value = _skill_ok(
        "# Report\n" + _FINGERPRINT_START + "\nfp1\n" + _FINGERPRINT_END
    )
    tool_ctx.executor = mock_executor

    mock_gh = MagicMock()
    mock_gh.has_token = True
    mock_gh.search_issues = AsyncMock(side_effect=RuntimeError("network failure"))
    tool_ctx.github_client = mock_gh

    # Must not raise — exception is captured in github dict
    result = json.loads(await report_bug("err", str(tmp_path), severity="blocking"))
    assert result["success"] is True  # session succeeded
    assert result["github"].get("skipped") is True
    assert "unexpected error" in result["github"].get("reason", "")
    assert "network failure" in result["github"].get("reason", "")
