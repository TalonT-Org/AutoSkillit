"""Tests for peak_context and turn_count extraction from extract_token_usage."""

from __future__ import annotations

import json

import pytest

from autoskillit.execution.session._session_model import extract_token_usage

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _assistant(cache_read: int = 0) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": cache_read,
                },
            },
        }
    )


def _result(cache_read: int = 0) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "session_id": "s1",
            "errors": [],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": cache_read,
            },
        }
    )


def _build_ndjson(lines: list[str]) -> str:
    return "\n".join(lines)


def test_extract_peak_context_from_multi_turn_stdout():
    stdout = _build_ndjson(
        [
            _assistant(cache_read=10000),
            _assistant(cache_read=50000),
            _assistant(cache_read=30000),
        ]
    )
    result = extract_token_usage(stdout)
    assert result is not None
    assert result["peak_context"] == 50000


def test_extract_turn_count_from_multi_turn_stdout():
    stdout = _build_ndjson(
        [
            _assistant(cache_read=10000),
            _assistant(cache_read=20000),
            _assistant(cache_read=30000),
        ]
    )
    result = extract_token_usage(stdout)
    assert result is not None
    assert result["turn_count"] == 3


def test_extract_peak_context_single_turn():
    stdout = _build_ndjson([_assistant(cache_read=42000)])
    result = extract_token_usage(stdout)
    assert result is not None
    assert result["peak_context"] == 42000
    assert result["turn_count"] == 1


def test_extract_peak_context_with_result_record():
    stdout = _build_ndjson(
        [
            _assistant(cache_read=60000),
            _assistant(cache_read=80000),
            _result(cache_read=140000),
        ]
    )
    result = extract_token_usage(stdout)
    assert result is not None
    assert result["peak_context"] == 80000
    assert result["turn_count"] == 2


def test_extract_peak_context_no_assistant_records():
    stdout = _build_ndjson([_result(cache_read=100000)])
    result = extract_token_usage(stdout)
    assert result is not None
    assert result["peak_context"] == 0
    assert result["turn_count"] == 0


def test_extract_peak_context_zero_cache_read():
    stdout = _build_ndjson(
        [
            _assistant(cache_read=0),
            _assistant(cache_read=0),
        ]
    )
    result = extract_token_usage(stdout)
    assert result is not None
    assert result["peak_context"] == 0
    assert result["turn_count"] == 2
