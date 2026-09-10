"""Tests for peak_context and turn_count extraction from extract_token_usage."""

from __future__ import annotations

import json

import pytest

from autoskillit.execution.session._session_model import extract_token_usage

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _assistant(
    cache_read: object = 0,
    *,
    message_id: str | None = None,
    request_id: str | None = None,
    timestamp: str | None = None,
    model: str = "claude-sonnet-4-6",
    **counters: object,
) -> str:
    usage = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_write_tokens": 0,
        "cache_read_tokens": cache_read,
    }
    usage.update(counters)
    message: dict[str, object] = {"model": model, "usage": usage}
    if message_id is not None:
        message["id"] = message_id
    record: dict[str, object] = {"type": "assistant", "message": message}
    if request_id is not None:
        record["requestId"] = request_id
    if timestamp is not None:
        record["timestamp"] = timestamp
    return json.dumps(record)


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
                "cache_write_tokens": 0,
                "cache_read_tokens": cache_read,
            },
        }
    )


def _model_usage_result(model_usage: dict[str, dict[str, object]]) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "session_id": "s1",
            "errors": [],
            "modelUsage": model_usage,
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
    usage, _rows = extract_token_usage(stdout)
    assert usage is not None
    assert usage["peak_context"] == 50000


def test_extract_turn_count_from_multi_turn_stdout():
    stdout = _build_ndjson(
        [
            _assistant(cache_read=10000),
            _assistant(cache_read=20000),
            _assistant(cache_read=30000),
        ]
    )
    usage, _rows = extract_token_usage(stdout)
    assert usage is not None
    assert usage["turn_count"] == 3


def test_extract_peak_context_single_turn():
    stdout = _build_ndjson([_assistant(cache_read=42000)])
    usage, _rows = extract_token_usage(stdout)
    assert usage is not None
    assert usage["peak_context"] == 42000
    assert usage["turn_count"] == 1


def test_extract_peak_context_with_result_record():
    stdout = _build_ndjson(
        [
            _assistant(cache_read=60000),
            _assistant(cache_read=80000),
            _result(cache_read=140000),
        ]
    )
    usage, _rows = extract_token_usage(stdout)
    assert usage is not None
    assert usage["peak_context"] == 80000
    assert usage["turn_count"] == 2


def test_extract_peak_context_no_assistant_records():
    stdout = _build_ndjson([_result(cache_read=100000)])
    usage, _rows = extract_token_usage(stdout)
    assert usage is not None
    assert usage["peak_context"] == 0
    assert usage["turn_count"] == 0


def test_extract_peak_context_zero_cache_read():
    stdout = _build_ndjson(
        [
            _assistant(cache_read=0),
            _assistant(cache_read=0),
        ]
    )
    usage, _rows = extract_token_usage(stdout)
    assert usage is not None
    assert usage["peak_context"] == 0
    assert usage["turn_count"] == 2


def test_duplicate_message_snapshots_count_once_in_rows_and_aggregate():
    lines: list[str] = []
    for index in range(1, 6):
        model = "claude-opus-4-6" if index <= 3 else "claude-sonnet-4-6"
        line = _assistant(
            cache_read=index * 100,
            message_id=f"msg-{index}",
            request_id=f"req-{index}",
            timestamp=f"2026-09-10T12:00:0{index}Z",
            model=model,
            input_tokens=index,
            output_tokens=index * 10,
            cache_write_tokens=index,
        )
        lines.extend([line] * (2 if index < 5 else 1))

    usage, rows = extract_token_usage(_build_ndjson(lines))

    assert len(lines) == 9
    assert usage is not None
    assert usage["turn_count"] == 5
    assert usage["input_tokens"] == 15
    assert usage["output_tokens"] == 150
    assert usage["cache_read_tokens"] == 1_500
    assert usage["cache_write_tokens"] == 15
    assert usage["peak_context"] == 500
    assert usage["model_breakdown"]["claude-opus-4-6"]["input_tokens"] == 6
    assert usage["model_breakdown"]["claude-sonnet-4-6"]["input_tokens"] == 9
    assert [row["message_id"] for row in rows] == [f"msg-{index}" for index in range(1, 6)]


def test_rows_use_exact_model_windows_and_inclusive_input():
    opus = "claude-opus-4-6-20260901"
    sonnet = "claude-sonnet-4-6-20260901"
    stdout = _build_ndjson(
        [
            _assistant(
                cache_read=100,
                message_id="opus-message",
                model=opus,
                input_tokens=10,
                cache_write_tokens=5,
            ),
            _assistant(
                cache_read=100,
                message_id="sonnet-message",
                model=sonnet,
                input_tokens=20,
                cache_write_tokens=5,
            ),
            _model_usage_result(
                {
                    opus: {"contextWindow": 50},
                    sonnet: {"contextWindow": 200},
                }
            ),
        ]
    )

    usage, rows = extract_token_usage(stdout)

    assert usage is not None
    assert usage["input_tokens"] == 30
    assert [row["backend"] for row in rows] == ["claude-code", "claude-code"]
    assert [row["model"] for row in rows] == [opus, sonnet]
    assert [row["input_tokens"] for row in rows] == [115, 125]
    assert [row["context_window_tokens"] for row in rows] == [50, 200]
    assert [row["context_fraction"] for row in rows] == [2.0, 0.5]


@pytest.mark.parametrize("window", [None, 0, -1, True, 200.0, "200"])
def test_unknown_or_invalid_context_window_leaves_fraction_unknown(
    window: object,
):
    model = "another-model" if window is None else "claude-sonnet-4-6"
    stdout = _build_ndjson(
        [
            _assistant(cache_read=100, message_id="message-1"),
            _model_usage_result({model: {"contextWindow": 200 if window is None else window}}),
        ]
    )

    _usage, rows = extract_token_usage(stdout)

    assert rows[0]["context_window_tokens"] is None
    assert rows[0]["context_fraction"] is None


def test_conflicting_windows_invalidate_only_the_affected_model():
    opus = "claude-opus-4-6"
    sonnet = "claude-sonnet-4-6"
    stdout = _build_ndjson(
        [
            _assistant(cache_read=100, message_id="opus-message", model=opus),
            _assistant(cache_read=100, message_id="sonnet-message", model=sonnet),
            _model_usage_result({opus: {"contextWindow": 200}, sonnet: {"contextWindow": 400}}),
            _model_usage_result({opus: {"contextWindow": 300}, sonnet: {"contextWindow": 400}}),
        ]
    )

    _usage, rows = extract_token_usage(stdout)

    assert rows[0]["context_window_tokens"] is None
    assert rows[0]["context_fraction"] is None
    assert rows[1]["context_window_tokens"] == 400
    assert rows[1]["context_fraction"] == 0.25
