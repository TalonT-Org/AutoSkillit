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
from autoskillit.execution.headless._headless_result import _adapt_agent_result
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
        {"name": "file_change", "type": "file_change", "path": "a.py"},
        {"name": "file_change", "type": "file_change", "path": "b.py"},
    ]


def test_tool_uses_missing_sources_default_empty() -> None:
    result = _adapt_agent_result(_make_agent_result())
    assert result.tool_uses == []


def test_file_change_entry_shape() -> None:
    result = _adapt_agent_result(_make_agent_result(raw={"file_changes": ["x.py"]}))
    entry = result.tool_uses[0]
    assert set(entry.keys()) == {"name", "type", "path"}
    assert entry["name"] == "file_change"
    assert entry["type"] == "file_change"
    assert entry["path"] == "x.py"


def test_assistant_messages_from_raw() -> None:
    result = _adapt_agent_result(_make_agent_result(raw={"agent_messages": ["msg1", "msg2"]}))
    assert result.assistant_messages == ["msg1", "msg2"]


def test_assistant_messages_default_empty() -> None:
    result = _adapt_agent_result(_make_agent_result())
    assert result.assistant_messages == []
