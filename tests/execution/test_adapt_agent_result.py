"""Tests for _adapt_agent_result."""

from __future__ import annotations

from typing import Any

import pytest

from autoskillit.core.types import (
    AgentSessionResult,
    CliSubtype,
    InfraExitCategory,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.headless._headless_evidence import _adapt_agent_result
from autoskillit.execution.session._exit_classification import classify_infra_exit

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _make_agent_result(**kwargs: Any) -> AgentSessionResult:
    defaults = {
        "success": True,
        "exit_code": 0,
        "backend_name": "codex",
        "elapsed_seconds": 1.0,
        "session_id": "test-session-1",
        "output": "done",
        "error": "",
        "raw": {},
    }
    defaults.update(kwargs)
    return AgentSessionResult(**defaults)


def test_success_maps_is_error_false() -> None:
    result = _adapt_agent_result(_make_agent_result(success=True))
    assert result.is_error is False


def test_raw_is_error_overrides_success() -> None:
    result = _adapt_agent_result(_make_agent_result(success=True, raw={"is_error": True}))
    assert result.is_error is True


def test_session_id_none_becomes_empty_string() -> None:
    result = _adapt_agent_result(_make_agent_result(session_id=None))
    assert result.session_id == ""


def test_session_id_preserved() -> None:
    result = _adapt_agent_result(_make_agent_result(session_id="abc-123"))
    assert result.session_id == "abc-123"


def test_hardcoded_thinking_defaults() -> None:
    result = _adapt_agent_result(_make_agent_result())
    assert result.has_thinking_only_turn is False
    assert result.seen_block_types == frozenset()


def test_unknown_subtype_maps_to_unknown() -> None:
    result = _adapt_agent_result(_make_agent_result(raw={"subtype": "some_new_thing"}))
    assert result.subtype == CliSubtype.UNKNOWN


def test_known_subtype_preserved() -> None:
    result = _adapt_agent_result(_make_agent_result(raw={"subtype": "success"}))
    assert result.subtype == CliSubtype.SUCCESS


def test_output_maps_to_result() -> None:
    result = _adapt_agent_result(_make_agent_result(output="hello world"))
    assert result.result == "hello world"


def test_stop_reasons_from_raw() -> None:
    result = _adapt_agent_result(
        _make_agent_result(raw={"stop_reasons": ["end_turn", "max_tokens"]})
    )
    assert result.stop_reasons == ["end_turn", "max_tokens"]


def test_stop_reasons_default_empty() -> None:
    result = _adapt_agent_result(_make_agent_result())
    assert result.stop_reasons == []


def test_context_exhaustion_detected() -> None:
    result = _adapt_agent_result(
        _make_agent_result(
            raw={"subtype": "error_during_execution"},
            error="API error: context_length_exceeded",
        )
    )
    assert result.jsonl_context_exhausted is True


def test_context_exhaustion_requires_both_conditions() -> None:
    result = _adapt_agent_result(
        _make_agent_result(
            raw={"subtype": "success"},
            error="context_length_exceeded",
        )
    )
    assert result.jsonl_context_exhausted is False


def test_context_exhaustion_no_error_string() -> None:
    result = _adapt_agent_result(
        _make_agent_result(
            raw={"subtype": "error_during_execution"},
            error="",
        )
    )
    assert result.jsonl_context_exhausted is False


def test_classify_infra_exit_context_exhausted() -> None:
    adapted = _adapt_agent_result(
        _make_agent_result(
            raw={"subtype": "error_during_execution"},
            error="API error: context_length_exceeded",
        )
    )
    subprocess_result = SubprocessResult(
        returncode=1,
        stdout="",
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=1,
        session_id="",
        channel_b_session_id="",
    )
    exit_category = classify_infra_exit(adapted, subprocess_result)
    assert exit_category == InfraExitCategory.CONTEXT_EXHAUSTED


def test_canonical_token_usage_preferred() -> None:
    result = _adapt_agent_result(
        _make_agent_result(
            raw={
                "canonical_token_usage": {"input": 100},
                "token_usage": {"input": 50},
            }
        )
    )
    assert result.token_usage == {"input": 100}


def test_fallback_to_raw_token_usage() -> None:
    result = _adapt_agent_result(_make_agent_result(raw={"token_usage": {"input": 50}}))
    assert result.token_usage == {"input": 50}


def test_both_token_usage_absent() -> None:
    result = _adapt_agent_result(_make_agent_result())
    assert result.token_usage is None


def test_tool_uses_concatenation() -> None:
    result = _adapt_agent_result(
        _make_agent_result(
            raw={
                "command_executions": [{"name": "cmd"}],
                "mcp_tool_calls": [{"name": "mcp"}],
                "file_changes": ["a.py", "b.py"],
            }
        )
    )
    assert result.tool_uses == [
        {"name": "cmd"},
        {"name": "mcp"},
        {"name": "file_change", "type": "file_change", "file_path": "a.py"},
        {"name": "file_change", "type": "file_change", "file_path": "b.py"},
    ]


def test_tool_uses_missing_sources_default_empty() -> None:
    result = _adapt_agent_result(_make_agent_result())
    assert result.tool_uses == []


def test_file_change_entry_shape() -> None:
    result = _adapt_agent_result(_make_agent_result(raw={"file_changes": ["x.py"]}))
    entry = result.tool_uses[0]
    assert set(entry.keys()) == {"name", "type", "file_path"}
    assert entry["name"] == "file_change"
    assert entry["type"] == "file_change"
    assert entry["file_path"] == "x.py"


def test_assistant_messages_from_raw() -> None:
    result = _adapt_agent_result(_make_agent_result(raw={"agent_messages": ["msg1", "msg2"]}))
    assert result.assistant_messages == ["msg1", "msg2"]


def test_assistant_messages_default_empty() -> None:
    result = _adapt_agent_result(_make_agent_result())
    assert result.assistant_messages == []


def test_success_false_maps_is_error_true_when_raw_absent() -> None:
    result = _adapt_agent_result(_make_agent_result(success=False))
    assert result.is_error is True


def test_errors_field_defaults_to_empty_list() -> None:
    result = _adapt_agent_result(_make_agent_result())
    assert result.errors == []


def test_context_exhaustion_error_max_turns() -> None:
    result = _adapt_agent_result(
        _make_agent_result(
            raw={"subtype": "error_max_turns"},
            error="API error: context_length_exceeded",
        )
    )
    assert result.jsonl_context_exhausted is True


def test_context_exhaustion_unknown_subtype() -> None:
    result = _adapt_agent_result(
        _make_agent_result(
            error="context_length_exceeded",
        )
    )
    assert result.jsonl_context_exhausted is True


def test_context_exhaustion_error_is_none() -> None:
    result = _adapt_agent_result(
        _make_agent_result(
            raw={"subtype": "error_during_execution"},
            error=None,
        )
    )
    assert result.jsonl_context_exhausted is False


def test_unused_agent_fields_do_not_affect_output() -> None:
    base = _adapt_agent_result(
        _make_agent_result(exit_code=0, backend_name="a", elapsed_seconds=1.0)
    )
    varied = _adapt_agent_result(
        _make_agent_result(exit_code=99, backend_name="b", elapsed_seconds=999.0)
    )
    assert base.subtype == varied.subtype
    assert base.is_error == varied.is_error
    assert base.result == varied.result
    assert base.session_id == varied.session_id
    assert base.errors == varied.errors
    assert base.token_usage == varied.token_usage
    assert base.assistant_messages == varied.assistant_messages
    assert base.tool_uses == varied.tool_uses
    assert base.jsonl_context_exhausted == varied.jsonl_context_exhausted
    assert base.stop_reasons == varied.stop_reasons
    assert base.has_thinking_only_turn == varied.has_thinking_only_turn
    assert base.seen_block_types == varied.seen_block_types

    assert not hasattr(base, "exit_code")
    assert not hasattr(base, "backend_name")
    assert not hasattr(base, "elapsed_seconds")


def test_canonical_token_usage_none_falls_through_to_token_usage() -> None:
    result = _adapt_agent_result(
        _make_agent_result(raw={"canonical_token_usage": None, "token_usage": {"input": 42}})
    )
    assert result.token_usage == {"input": 42}


def test_empty_output_maps_to_empty_result() -> None:
    result = _adapt_agent_result(_make_agent_result(output=""))
    assert result.result == ""


def test_full_round_trip_all_fields() -> None:
    agent = _make_agent_result(
        success=True,
        exit_code=42,
        backend_name="codex",
        elapsed_seconds=5.5,
        session_id="sess-abc",
        output="final output",
        error="some warning",
        raw={
            "is_error": False,
            "subtype": "success",
            "stop_reasons": ["end_turn"],
            "canonical_token_usage": {"input": 200, "output": 50},
            "token_usage": {"input": 100},
            "command_executions": [{"name": "bash", "cmd": "ls"}],
            "mcp_tool_calls": [{"name": "read", "path": "/tmp"}],
            "file_changes": ["main.py"],
            "agent_messages": ["I updated the file."],
        },
    )
    result = _adapt_agent_result(agent)

    assert result.subtype == CliSubtype.SUCCESS
    assert result.is_error is False
    assert result.result == "final output"
    assert result.session_id == "sess-abc"
    assert result.errors == ["some warning"]
    assert result.token_usage == {"input": 200, "output": 50}
    assert result.assistant_messages == ["I updated the file."]
    assert result.tool_uses == [
        {"name": "bash", "cmd": "ls"},
        {"name": "read", "path": "/tmp"},
        {"name": "file_change", "type": "file_change", "file_path": "main.py"},
    ]
    assert result.jsonl_context_exhausted is False
    assert result.stop_reasons == ["end_turn"]
    assert result.has_thinking_only_turn is False
    assert result.seen_block_types == frozenset()


def test_context_exhaustion_from_code_field() -> None:
    result = _adapt_agent_result(
        _make_agent_result(
            raw={"subtype": "error_during_execution"},
            error="Token limit reached. [context_length_exceeded]",
        )
    )
    assert result.jsonl_context_exhausted is True


def test_rate_limit_code_field_not_context_exhausted() -> None:
    result = _adapt_agent_result(
        _make_agent_result(
            raw={"subtype": "error_during_execution"},
            error="Rate limit exceeded. [rate_limit_exceeded]",
        )
    )
    assert result.jsonl_context_exhausted is False


def test_errors_populated_from_agent_error() -> None:
    result = _adapt_agent_result(
        _make_agent_result(error="Rate limit exceeded. [rate_limit_exceeded]")
    )
    assert result.errors == ["Rate limit exceeded. [rate_limit_exceeded]"]


def test_context_exhaustion_via_structured_error_code() -> None:
    result = _adapt_agent_result(
        _make_agent_result(
            raw={"subtype": "error_during_execution", "error_code": "context_length_exceeded"},
            error="",
        )
    )
    assert result.jsonl_context_exhausted is True


def test_api_error_status_from_rate_limit_code() -> None:
    result = _adapt_agent_result(
        _make_agent_result(
            raw={"subtype": "error_during_execution", "error_code": "rate_limit_exceeded"},
            error="Rate limit exceeded",
        )
    )
    assert result.api_error_status == 429


def test_api_error_status_none_for_non_rate_limit_code() -> None:
    result = _adapt_agent_result(
        _make_agent_result(
            raw={"subtype": "error_during_execution", "error_code": "server_error"},
            error="Internal server error",
        )
    )
    assert result.api_error_status is None


def test_classify_infra_exit_rate_limited_via_adapted_error_code() -> None:
    adapted = _adapt_agent_result(
        _make_agent_result(
            raw={"subtype": "error_during_execution", "error_code": "rate_limit_exceeded"},
            error="Rate limit exceeded",
        )
    )
    subprocess_result = SubprocessResult(
        returncode=1,
        stdout="",
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=1,
        session_id="",
        channel_b_session_id="",
    )
    exit_category = classify_infra_exit(adapted, subprocess_result)
    assert exit_category == InfraExitCategory.RATE_LIMITED


class TestAdaptAgentResultFilePathKey:
    """Verify file_change entries use 'file_path' key for synthesis compatibility."""

    def test_file_change_entry_has_file_path_key(self) -> None:
        result = _adapt_agent_result(_make_agent_result(raw={"file_changes": ["x.py"]}))
        entry = result.tool_uses[0]
        assert set(entry.keys()) == {"name", "type", "file_path"}
        assert entry["file_path"] == "x.py"

    def test_file_path_key_enables_primary_synthesis_branch(self) -> None:
        from autoskillit.execution.headless import _synthesize_from_write_artifacts

        agent = _make_agent_result(raw={"file_changes": ["/abs/output.md"]})
        adapted = _adapt_agent_result(agent)
        recovered = _synthesize_from_write_artifacts(
            adapted,
            [r"plan_path\s*=\s*/.+"],
            write_call_count=0,
            file_changes=["/abs/output.md"],
            write_tool_names=frozenset({"file_change"}),
        )
        assert recovered is not None
        assert "plan_path = /abs/output.md" in recovered.result
