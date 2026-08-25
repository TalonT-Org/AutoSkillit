"""Tests for tools_issue_headless.py and tools_issue_labels.py pure helper functions."""

from __future__ import annotations

import json

import pytest

from autoskillit.core import RetryReason
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools.tools_issue_headless import (
    _build_headless_error_response,
    _build_prepare_skill_command,
    _parse_prepare_result,
    _retry_reason_to_error,
    _without_success_key,
)
from autoskillit.server.tools.tools_issue_labels import _extract_label_names
from tests.server._issue_lifecycle_test_helpers import _make_skill_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.fixture
def tool_ctx_kitchen_open(tool_ctx):
    """Open the gate while retaining production backend compatibility metadata."""
    tool_ctx.gate = DefaultGateState(enabled=True)
    return tool_ctx


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


def test_build_headless_error_response_propagates_extra_fields() -> None:
    """extra_fields merges partial-result data without overwriting the canonical 7 fields."""
    result = _make_skill_result(
        success=False,
        retry_reason=RetryReason.CONTRACT_RECOVERY,
        session_id="abc",
        subtype="contract_recovery",
    )
    resp = _build_headless_error_response(
        result,
        error="contract_recovery",
        extra_fields={
            "partial_issue_url": "https://github.com/owner/repo/issues/42",
            "partial_issue_number": 42,
        },
    )
    assert resp["partial_issue_url"] == "https://github.com/owner/repo/issues/42"
    assert resp["partial_issue_number"] == 42
    # Canonical 7 fields remain intact.
    assert resp["success"] is False
    assert resp["status"] == "failed"
    assert resp["error"] == "contract_recovery"
    assert resp["session_id"] == "abc"
    assert resp["stderr"] == ""
    assert resp["subtype"] == "contract_recovery"
    assert resp["exit_code"] == 0


def test_build_headless_error_response_extra_fields_cannot_override_canonical() -> None:
    """extra_fields cannot overwrite any of the 7 canonical keys (defense in depth)."""
    result = _make_skill_result(
        success=False,
        session_id="real-session",
        subtype="timeout",
        exit_code=1,
        stderr="real stderr",
    )
    resp = _build_headless_error_response(
        result,
        error="real error",
        extra_fields={
            "error": "injected",
            "session_id": "spoofed",
            "stderr": "injected stderr",
            "subtype": "injected subtype",
            "exit_code": 999,
            "status": "spoofed",
            "success": True,
        },
    )
    assert resp["error"] == "real error"
    assert resp["session_id"] == "real-session"
    assert resp["stderr"] == "real stderr"
    assert resp["subtype"] == "timeout"
    assert resp["exit_code"] == 1
    assert resp["status"] == "failed"
    assert resp["success"] is False


def test_build_headless_error_response_degraded_success_fields() -> None:
    """success=True, status='degraded' → warning key set, no error key."""
    result = _make_skill_result(success=True, session_id="abc", subtype="success", exit_code=0)
    resp = _build_headless_error_response(
        result, warning="no result block found", status="degraded", success=True
    )
    assert resp["success"] is True
    assert resp["status"] == "degraded"
    assert resp["warning"] == "no result block found"
    assert "error" not in resp
    assert resp["session_id"] == "abc"
    assert resp["subtype"] == "success"
    assert resp["exit_code"] == 0


def test_build_headless_error_response_degraded_extra_fields_cannot_override_canonical() -> None:
    """In degraded-success mode, extra_fields still cannot overwrite canonical keys."""
    result = _make_skill_result(
        success=True, session_id="real-session", subtype="success", exit_code=0
    )
    resp = _build_headless_error_response(
        result,
        warning="real warning",
        status="degraded",
        success=True,
        extra_fields={
            "warning": "injected",
            "session_id": "spoofed",
            "status": "spoofed",
            "success": False,
            "partial_issue_url": "https://github.com/owner/repo/issues/42",
        },
    )
    assert resp["warning"] == "real warning"
    assert resp["session_id"] == "real-session"
    assert resp["status"] == "degraded"
    assert resp["success"] is True
    assert resp["partial_issue_url"] == "https://github.com/owner/repo/issues/42"


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
    cmd = _build_prepare_skill_command("T", "B", "", False, False)
    assert cmd == "/prepare-issue\n\nTitle: T\n\nBody:\nB"


def test_build_prepare_skill_command_with_flags() -> None:
    """repo + dry_run + split → supported flags in output."""
    cmd = _build_prepare_skill_command("T", "B", "owner/repo", True, True)
    assert "--repo owner/repo" in cmd
    assert "--label" not in cmd
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
