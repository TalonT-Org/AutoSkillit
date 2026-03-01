"""Tests for session_result.py domain model.

No server import, no monkeypatch — all tested functions are pure.
"""

from __future__ import annotations

import json

import pytest
import structlog

from autoskillit.core.types import (
    CONTEXT_EXHAUSTION_MARKER,
    RetryReason,
    TerminationReason,
)
from autoskillit.execution.session import (
    ClaudeSessionResult,
    SkillResult,
    _compute_retry,
    _compute_success,
    _is_kill_anomaly,
    extract_token_usage,
    parse_session_result,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_success_session(result: str = "done") -> ClaudeSessionResult:
    return ClaudeSessionResult(
        subtype="success",
        is_error=False,
        result=result,
        session_id="s1",
    )


def _make_error_session(
    subtype: str = "error_during_execution",
    result: str = "failed",
    errors: list[str] | None = None,
) -> ClaudeSessionResult:
    return ClaudeSessionResult(
        subtype=subtype,
        is_error=True,
        result=result,
        session_id="s1",
        errors=errors or [],
    )


def _result_ndjson(
    result_text: str = "done",
    subtype: str = "success",
    is_error: bool = False,
    session_id: str = "s1",
    errors: list | None = None,
    usage: dict | None = None,
) -> str:
    obj: dict = {
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "result": result_text,
        "session_id": session_id,
        "errors": errors or [],
    }
    if usage:
        obj["usage"] = usage
    return json.dumps(obj)


def _assistant_ndjson(
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_create: int = 0,
    cache_read: int = 0,
) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "model": model,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_creation_input_tokens": cache_create,
                    "cache_read_input_tokens": cache_read,
                },
            },
        }
    )


# ---------------------------------------------------------------------------
# ClaudeSessionResult
# ---------------------------------------------------------------------------


class TestClaudeSessionResultBasic:
    def test_basic_fields(self):
        s = ClaudeSessionResult(
            subtype="success", is_error=False, result="hello", session_id="abc"
        )
        assert s.subtype == "success"
        assert s.is_error is False
        assert s.result == "hello"
        assert s.session_id == "abc"

    def test_post_init_coerces_list_content_to_str(self):
        s = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=[{"type": "text", "text": "hello"}],
            session_id="s1",
        )
        assert s.result == "hello"

    def test_post_init_coerces_none_result_to_empty_str(self):
        s = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=None,
            session_id="s1",  # type: ignore[arg-type]
        )
        assert s.result == ""

    def test_post_init_coerces_non_list_errors(self):
        s = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="ok",
            session_id="s1",
            errors=None,  # type: ignore[arg-type]
        )
        assert s.errors == []

    def test_post_init_list_with_non_dict_element(self):
        s = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=["plain string", {"type": "text", "text": "extra"}],
            session_id="s1",
        )
        assert s.result == "plain string\nextra"


class TestClaudeSessionResultContextExhausted:
    def test_is_context_exhausted_via_errors_list(self):
        s = ClaudeSessionResult(
            subtype="error",
            is_error=True,
            result="",
            session_id="s1",
            errors=[f"Request failed: {CONTEXT_EXHAUSTION_MARKER}"],
        )
        assert s._is_context_exhausted() is True

    def test_is_context_exhausted_via_result_text(self):
        s = ClaudeSessionResult(
            subtype="success",
            is_error=True,
            result=f"Something: {CONTEXT_EXHAUSTION_MARKER}",
            session_id="s1",
        )
        assert s._is_context_exhausted() is True

    def test_is_context_exhausted_false_when_no_error(self):
        # is_error=False means context exhaustion cannot be triggered
        s = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"contains {CONTEXT_EXHAUSTION_MARKER} text",
            session_id="s1",
        )
        assert s._is_context_exhausted() is False


class TestClaudeSessionResultAgentResult:
    def test_agent_result_returns_override_on_context_exhaustion(self):
        s = ClaudeSessionResult(
            subtype="error",
            is_error=True,
            result="raw output",
            session_id="s1",
            errors=[CONTEXT_EXHAUSTION_MARKER],
        )
        assert "Context limit reached" in s.agent_result

    def test_agent_result_returns_override_on_max_turns(self):
        s = ClaudeSessionResult(
            subtype="error_max_turns",
            is_error=False,
            result="raw output",
            session_id="s1",
        )
        assert "Turn limit reached" in s.agent_result

    def test_agent_result_returns_result_on_success(self):
        s = _make_success_session("Task done.")
        assert s.agent_result == "Task done."


class TestClaudeSessionResultNeedsRetry:
    def test_needs_retry_true_for_max_turns(self):
        s = ClaudeSessionResult(
            subtype="error_max_turns", is_error=False, result="r", session_id="s1"
        )
        assert s.needs_retry is True

    def test_needs_retry_true_for_context_exhausted(self):
        s = ClaudeSessionResult(
            subtype="error",
            is_error=True,
            result="",
            session_id="s1",
            errors=[CONTEXT_EXHAUSTION_MARKER],
        )
        assert s.needs_retry is True

    def test_needs_retry_false_for_success(self):
        s = _make_success_session()
        assert s.needs_retry is False

    def test_retry_reason_resume_when_needs_retry(self):
        s = ClaudeSessionResult(
            subtype="error_max_turns", is_error=False, result="r", session_id="s1"
        )
        assert s.retry_reason == RetryReason.RESUME

    def test_retry_reason_none_when_no_retry(self):
        s = _make_success_session()
        assert s.retry_reason == RetryReason.NONE


# ---------------------------------------------------------------------------
# parse_session_result
# ---------------------------------------------------------------------------


class TestParseSessionResult:
    def test_empty_string_returns_empty_output_subtype(self):
        result = parse_session_result("")
        assert result.subtype == "empty_output"
        assert result.is_error is True

    def test_whitespace_only_returns_empty_output_subtype(self):
        result = parse_session_result("   \n\t  ")
        assert result.subtype == "empty_output"

    def test_valid_ndjson_last_result_wins(self):
        first = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "first",
                "is_error": False,
                "session_id": "a",
            }
        )
        second = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "second",
                "is_error": False,
                "session_id": "b",
            }
        )
        result = parse_session_result(first + "\n" + second)
        assert result.result == "second"
        assert result.session_id == "b"

    def test_no_result_type_line_returns_unparseable(self):
        result = parse_session_result(
            json.dumps({"type": "assistant", "message": {"content": "hello"}})
        )
        assert result.subtype == "unparseable"

    def test_non_json_returns_unparseable(self):
        result = parse_session_result("Traceback (most recent call last):\n  boom")
        assert result.subtype == "unparseable"
        assert result.is_error is True

    def test_fallback_single_json_object(self):
        single = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "ok",
                "is_error": False,
                "session_id": "s",
            }
        )
        result = parse_session_result(single)
        assert result.result == "ok"

    def test_populates_token_usage(self):
        assistant = _assistant_ndjson(input_tokens=100, output_tokens=50)
        result_rec = _result_ndjson(
            usage={
                "input_tokens": 200,
                "output_tokens": 80,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
        )
        stdout = assistant + "\n" + result_rec
        result = parse_session_result(stdout)
        assert result.token_usage is not None
        assert result.token_usage["input_tokens"] == 200

    def test_logs_unknown_result_keys_at_debug(self):
        # Build a result record with an extra key not in _KNOWN_RESULT_KEYS
        record = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "abc",
                "errors": [],
                "future_field": "x",
            }
        )
        with structlog.testing.capture_logs() as logs:
            parse_session_result(record)
        debug_entries = [e for e in logs if e.get("log_level") == "debug"]
        assert any(e.get("event") == "unknown_result_keys" for e in debug_entries)
        matched = next(e for e in debug_entries if e.get("event") == "unknown_result_keys")
        assert "future_field" in matched.get("unknown_fields", [])

    def test_no_debug_log_for_known_result_keys(self):
        # Build a result record with only known keys — no debug log expected
        record = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "abc",
                "errors": [],
            }
        )
        with structlog.testing.capture_logs() as logs:
            parse_session_result(record)
        assert not any(e.get("event") == "unknown_result_keys" for e in logs)

    def test_parse_session_result_captures_assistant_messages(self):
        """parse_session_result populates assistant_messages from assistant-type NDJSON records."""
        ndjson = (
            '{"type":"assistant","message":{"role":"assistant","content":"Full report here."}}\n'
            '{"type":"result","subtype":"success","result":"%%ORDER_UP%%",'
            '"session_id":"s1","is_error":false}\n'
        )
        result = parse_session_result(ndjson)
        assert result.result == "%%ORDER_UP%%"
        assert result.assistant_messages == ["Full report here."]

    def test_parse_session_result_collects_multiple_assistant_messages(self):
        """All assistant records are collected, including the marker-only final one."""
        ndjson = (
            '{"type":"assistant","message":{"role":"assistant","content":"GO verdict."}}\n'
            '{"type":"assistant","message":{"role":"assistant","content":"%%ORDER_UP%%"}}\n'
            '{"type":"result","subtype":"success","result":"%%ORDER_UP%%",'
            '"session_id":"s1","is_error":false}\n'
        )
        result = parse_session_result(ndjson)
        assert result.assistant_messages == ["GO verdict.", "%%ORDER_UP%%"]

    def test_parse_session_result_assistant_messages_empty_when_no_assistant_records(self):
        """Baseline: no assistant records → assistant_messages is empty list."""
        ndjson = (
            '{"type":"result","subtype":"success","result":"Done.\\n\\n%%ORDER_UP%%",'
            '"session_id":"s1","is_error":false}\n'
        )
        result = parse_session_result(ndjson)
        assert result.assistant_messages == []


# ---------------------------------------------------------------------------
# extract_token_usage
# ---------------------------------------------------------------------------


class TestExtractTokenUsage:
    def test_empty_returns_none(self):
        assert extract_token_usage("") is None

    def test_no_records_returns_none(self):
        assert extract_token_usage("not json at all") is None

    def test_reads_from_result_record(self):
        stdout = _result_ndjson(
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
        )
        result = extract_token_usage(stdout)
        assert result is not None
        assert result["input_tokens"] == 10
        assert result["output_tokens"] == 5

    def test_reads_from_assistant_records_when_no_result(self):
        stdout = _assistant_ndjson(input_tokens=100, output_tokens=50)
        result = extract_token_usage(stdout)
        assert result is not None
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50

    def test_prefers_result_record_over_assistant_totals(self):
        assistant = _assistant_ndjson(input_tokens=100, output_tokens=50)
        result_rec = _result_ndjson(
            usage={
                "input_tokens": 999,
                "output_tokens": 888,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
        )
        result = extract_token_usage(assistant + "\n" + result_rec)
        assert result is not None
        assert result["input_tokens"] == 999

    def test_model_breakdown_populated(self):
        stdout = _assistant_ndjson(model="claude-opus-4-6", input_tokens=50, output_tokens=25)
        result = extract_token_usage(stdout)
        assert result is not None
        assert "model_breakdown" in result
        assert "claude-opus-4-6" in result["model_breakdown"]

    def test_skips_malformed_lines(self):
        malformed = "not json\n" + _assistant_ndjson(input_tokens=10, output_tokens=5)
        result = extract_token_usage(malformed)
        assert result is not None
        assert result["input_tokens"] == 10


class TestExtractTokenUsageArchitecture:
    """Contract tests asserting extract_token_usage's construction-time role."""

    def test_token_usage_on_parsed_result_matches_standalone_extract(self):
        """parse_session_result.token_usage == extract_token_usage(stdout).

        This is the architectural contract that makes extract_token_usage(stdout: str)
        the correct signature: the function is called during ClaudeSessionResult
        construction, before the object exists. A (result: ClaudeSessionResult)
        parameter would create a circular bootstrapping dependency.
        """
        assistant = _assistant_ndjson(input_tokens=100, output_tokens=50, cache_create=10)
        result_rec = _result_ndjson(
            usage={
                "input_tokens": 999,
                "output_tokens": 888,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 0,
            }
        )
        stdout = assistant + "\n" + result_rec

        parsed = parse_session_result(stdout)
        standalone = extract_token_usage(stdout)

        assert parsed.token_usage == standalone

    def test_token_usage_none_when_no_usage_in_stdout(self):
        """ClaudeSessionResult.token_usage is None when stdout has no usage data."""
        stdout = _result_ndjson()  # no usage key in result record
        parsed = parse_session_result(stdout)
        assert parsed.token_usage is None


# ---------------------------------------------------------------------------
# _compute_success
# ---------------------------------------------------------------------------


class TestComputeSuccess:
    def test_timed_out_always_false(self):
        s = _make_success_session("done")
        assert _compute_success(s, 0, TerminationReason.TIMED_OUT) is False

    def test_stale_always_false(self):
        s = _make_success_session("done")
        assert _compute_success(s, 0, TerminationReason.STALE) is False

    def test_nonzero_returncode_false_unless_recoverable(self):
        s = _make_error_session(subtype="error_during_execution", result="failed")
        assert _compute_success(s, 1, TerminationReason.NATURAL_EXIT) is False

    def test_nonzero_returncode_recoverable_path(self):
        s = ClaudeSessionResult(
            subtype="success", is_error=False, result="great output", session_id="s1"
        )
        assert _compute_success(s, 1, TerminationReason.COMPLETED) is True

    def test_is_error_false(self):
        s = _make_error_session(subtype="error_during_execution", result="bad")
        assert _compute_success(s, 0, TerminationReason.NATURAL_EXIT) is False

    def test_empty_result_false(self):
        s = ClaudeSessionResult(subtype="success", is_error=False, result="", session_id="s1")
        assert _compute_success(s, 0, TerminationReason.NATURAL_EXIT) is False

    def test_failure_subtype_false(self):
        for subtype in ("unknown", "empty_output", "unparseable", "timeout"):
            s = ClaudeSessionResult(
                subtype=subtype, is_error=False, result="some text", session_id="s1"
            )
            assert _compute_success(s, 0, TerminationReason.NATURAL_EXIT) is False

    def test_missing_completion_marker_false(self):
        s = _make_success_session("result without marker")
        assert (
            _compute_success(s, 0, TerminationReason.NATURAL_EXIT, completion_marker="%%DONE%%")
            is False
        )

    def test_result_is_only_marker_false(self):
        marker = "%%DONE%%"
        s = _make_success_session(marker)
        assert (
            _compute_success(s, 0, TerminationReason.NATURAL_EXIT, completion_marker=marker)
            is False
        )

    def test_nominal_true(self):
        s = _make_success_session("Task complete.")
        assert _compute_success(s, 0, TerminationReason.NATURAL_EXIT) is True


class TestComputeSuccessRealisticInputs:
    """_compute_success contracts using parse_session_result() as input constructor.

    Validates the end-to-end adjudication path with sessions that carry
    the field values that actually emerge from the parse pipeline — not
    hand-crafted objects with synthetic field combinations.
    """

    def test_empty_stdout_parses_to_empty_output_adjudicates_false(self):
        """parse_session_result('') → empty_output (is_error=True, result='') → False."""
        session = parse_session_result("")
        assert session.subtype == "empty_output"
        assert session.is_error is True
        assert _compute_success(session, 0, TerminationReason.NATURAL_EXIT) is False

    def test_garbled_stdout_parses_to_unparseable_adjudicates_false(self):
        """parse_session_result(garbled) → unparseable (is_error=True, result=stdout) → False."""
        session = parse_session_result("Traceback (most recent call last):\n  boom\n")
        assert session.subtype == "unparseable"
        assert session.is_error is True
        assert _compute_success(session, 0, TerminationReason.NATURAL_EXIT) is False

    def test_empty_stdout_not_bypassed_by_completed_path(self):
        """COMPLETED bypass requires subtype='success' AND result.strip().
        empty_output from parse_session_result('') fails both conditions → False.
        """
        session = parse_session_result("")
        assert _compute_success(session, -15, TerminationReason.COMPLETED) is False

    def test_unparseable_not_bypassed_by_completed_path(self):
        """COMPLETED bypass requires subtype='success'. unparseable → False."""
        session = parse_session_result("garbled output not json\n")
        assert _compute_success(session, -15, TerminationReason.COMPLETED) is False


# ---------------------------------------------------------------------------
# _compute_retry
# ---------------------------------------------------------------------------


class TestComputeRetry:
    def test_session_needs_retry_returns_resume(self):
        s = ClaudeSessionResult(
            subtype="error_max_turns", is_error=False, result="partial", session_id="s1"
        )
        needs, reason = _compute_retry(s, 1, TerminationReason.NATURAL_EXIT)
        assert needs is True
        assert reason == RetryReason.RESUME

    def test_empty_output_clean_exit_returns_resume(self):
        s = ClaudeSessionResult(subtype="empty_output", is_error=True, result="", session_id="")
        needs, reason = _compute_retry(s, 0, TerminationReason.NATURAL_EXIT)
        assert needs is True
        assert reason == RetryReason.RESUME

    def test_unparseable_on_completed_returns_resume(self):
        s = ClaudeSessionResult(
            subtype="unparseable", is_error=True, result="garbled", session_id=""
        )
        needs, reason = _compute_retry(s, -15, TerminationReason.COMPLETED)
        assert needs is True
        assert reason == RetryReason.RESUME

    def test_no_conditions_returns_false_none(self):
        s = _make_success_session("done")
        needs, reason = _compute_retry(s, 0, TerminationReason.NATURAL_EXIT)
        assert needs is False
        assert reason == RetryReason.NONE

    def test_compute_retry_success_empty_natural_exit_zero_rc_is_retriable(self):
        """
        Regression: CLAUDE_CODE_EXIT_AFTER_STOP_DELAY causes NATURAL_EXIT with
        subtype='success' and an empty result field. The CLI writes a valid result
        envelope header before the timer fires, leaving result=''. This must retry.
        """
        session = ClaudeSessionResult(
            subtype="success",
            result="",
            is_error=False,
            session_id="abc",
            errors=[],
        )
        needs_retry, reason = _compute_retry(
            session, returncode=0, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs_retry is True
        assert reason == RetryReason.RESUME



# ---------------------------------------------------------------------------
# _compute_retry exhaustiveness guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("termination", list(TerminationReason))
def test_compute_retry_handles_all_termination_reasons_without_raising(
    termination: TerminationReason,
) -> None:
    """
    Exhaustiveness guard: _compute_retry must return a valid (bool, RetryReason)
    for every current and future TerminationReason value. If assert_never fires
    for an unhandled value, this parametrize test catches it at collect-time.
    """
    session = ClaudeSessionResult(
        subtype="success",
        result="done. %%ORDER_UP%%",
        is_error=False,
        session_id="abc",
        errors=[],
    )
    result = _compute_retry(session, returncode=0, termination=termination)
    assert isinstance(result, tuple) and len(result) == 2
    assert isinstance(result[0], bool)
    assert isinstance(result[1], RetryReason)


@pytest.mark.parametrize("termination", list(TerminationReason))
def test_compute_success_handles_all_termination_reasons_without_raising(
    termination: TerminationReason,
) -> None:
    """
    Exhaustiveness guard for _compute_success: must return a defined bool
    for every TerminationReason without raising.
    """
    session = ClaudeSessionResult(
        subtype="success",
        result="done. %%ORDER_UP%%",
        is_error=False,
        session_id="abc",
        errors=[],
    )
    result = _compute_success(
        session,
        returncode=0,
        termination=termination,
        completion_marker="%%ORDER_UP%%",
    )
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _is_kill_anomaly
# ---------------------------------------------------------------------------


def test_is_kill_anomaly_returns_true_for_interrupted_subtype():
    """
    'interrupted' is a real Claude CLI subtype produced when the process is
    killed mid-generation. Under COMPLETED termination, this is a kill-race
    artifact that must be classified as retriable.
    """
    session = ClaudeSessionResult(
        subtype="interrupted",
        result="",
        is_error=True,
        session_id="abc",
        errors=[],
    )
    assert _is_kill_anomaly(session) is True


# ---------------------------------------------------------------------------
# SkillResult
# ---------------------------------------------------------------------------


class TestSkillResult:
    def _make(self, **overrides) -> SkillResult:
        defaults = dict(
            success=True,
            result="done",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
            token_usage=None,
        )
        return SkillResult(**{**defaults, **overrides})

    def test_to_json_produces_all_required_keys(self):
        sr = self._make()
        parsed = json.loads(sr.to_json())
        expected = {
            "success",
            "result",
            "session_id",
            "subtype",
            "is_error",
            "exit_code",
            "needs_retry",
            "retry_reason",
            "stderr",
            "token_usage",
        }
        assert set(parsed.keys()) == expected

    def test_to_json_retry_reason_serializes_as_string(self):
        sr = self._make(needs_retry=True, retry_reason=RetryReason.RESUME)
        parsed = json.loads(sr.to_json())
        assert parsed["retry_reason"] == "resume"

    def test_to_json_is_valid_json(self):
        sr = self._make(token_usage={"input_tokens": 10, "model_breakdown": {}})
        result = sr.to_json()
        parsed = json.loads(result)
        assert parsed["token_usage"]["input_tokens"] == 10

    def test_to_json_none_retry_reason_serializes_as_string(self):
        sr = self._make(retry_reason=RetryReason.NONE)
        parsed = json.loads(sr.to_json())
        assert parsed["retry_reason"] == "none"

    def test_to_json_token_usage_none(self):
        sr = self._make(token_usage=None)
        parsed = json.loads(sr.to_json())
        assert parsed["token_usage"] is None
