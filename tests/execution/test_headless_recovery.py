"""Tests for _headless_recovery.py skip-check normalization correctness."""

from __future__ import annotations

import json

import pytest

from autoskillit.execution.backends.claude import ClaudeResultParser
from autoskillit.execution.headless import (
    _extract_missing_token_hints,
    _synthesize_from_write_artifacts,
)
from autoskillit.execution.session import ClaudeSessionResult

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _make_session(result: str, file_path: str = "/tmp/out.md") -> ClaudeSessionResult:
    return ClaudeSessionResult(
        subtype="success",
        is_error=False,
        result=result,
        session_id="test-session",
        tool_uses=[{"name": "Write", "id": "t0", "file_path": file_path}],
    )


def _ndjson(result_text: str, file_path: str = "/tmp/out.md") -> str:
    content = [
        {"type": "tool_use", "name": "Write", "id": "t0", "input": {"file_path": file_path}}
    ]
    records = [
        json.dumps({"type": "assistant", "message": {"content": content}}),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": result_text,
                "session_id": "test-session",
            }
        ),
    ]
    return "\n".join(records)


class TestSynthesizeFromWriteArtifactsSkipsDecoratedToken:
    def test_plain_token_skips_synthesis(self):
        session = _make_session("plan_path = /tmp/out.md\n%%ORDER_UP%%")
        result = _synthesize_from_write_artifacts(
            session, [r"plan_path\s*=\s*/.+"], write_call_count=1
        )
        assert result is None

    def test_bold_decorated_token_skips_synthesis(self):
        session = _make_session("**plan_path** = /tmp/out.md\n%%ORDER_UP%%")
        result = _synthesize_from_write_artifacts(
            session, [r"plan_path\s*=\s*/.+"], write_call_count=1
        )
        assert result is None

    def test_backtick_key_decorated_token_skips_synthesis(self):
        session = _make_session("`plan_path` = /tmp/out.md\n%%ORDER_UP%%")
        result = _synthesize_from_write_artifacts(
            session, [r"plan_path\s*=\s*/.+"], write_call_count=1
        )
        assert result is None

    def test_code_fenced_token_skips_synthesis(self):
        session = _make_session("```\nplan_path = /tmp/out.md\n```\n%%ORDER_UP%%")
        result = _synthesize_from_write_artifacts(
            session, [r"plan_path\s*=\s*/.+"], write_call_count=1
        )
        assert result is None

    def test_absent_token_still_synthesizes(self):
        session = _make_session("plan summary\n%%ORDER_UP%%")
        result = _synthesize_from_write_artifacts(
            session, [r"plan_path\s*=\s*/.+"], write_call_count=1
        )
        assert result is not None
        assert "plan_path = /tmp/out.md" in result.result


class TestExtractMissingTokenHintsSkipsDecoratedToken:
    def test_bold_decorated_token_produces_no_hint(self):
        stdout = _ndjson("**plan_path** = /tmp/out.md\n%%ORDER_UP%%")
        hints = _extract_missing_token_hints(
            stdout, [r"plan_path\s*=\s*/.+"], ClaudeResultParser(), frozenset({"Write", "Edit"})
        )
        assert hints == []

    def test_backtick_key_decorated_token_produces_no_hint(self):
        stdout = _ndjson("`plan_path` = /tmp/out.md\n%%ORDER_UP%%")
        hints = _extract_missing_token_hints(
            stdout, [r"plan_path\s*=\s*/.+"], ClaudeResultParser(), frozenset({"Write", "Edit"})
        )
        assert hints == []

    def test_code_fenced_token_produces_no_hint(self):
        stdout = _ndjson("```\nplan_path = /tmp/out.md\n```\n%%ORDER_UP%%")
        hints = _extract_missing_token_hints(
            stdout, [r"plan_path\s*=\s*/.+"], ClaudeResultParser(), frozenset({"Write", "Edit"})
        )
        assert hints == []

    def test_absent_token_still_produces_hint(self):
        stdout = _ndjson("plan summary\n%%ORDER_UP%%")
        hints = _extract_missing_token_hints(
            stdout, [r"plan_path\s*=\s*/.+"], ClaudeResultParser(), frozenset({"Write", "Edit"})
        )
        assert hints == [("plan_path", "/tmp/out.md")]
