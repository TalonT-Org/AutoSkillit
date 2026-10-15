"""Tests for peak_context and turn_count extraction from extract_token_usage."""

from __future__ import annotations

import json

import pytest

from autoskillit.execution.session._session_model import extract_token_usage
from tests._helpers import UNKNOWN_MEASURE as _UNKNOWN
from tests._helpers import observed_measure as _observed

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


def _init(model: str | None) -> str:
    record: dict[str, object] = {
        "type": "system",
        "subtype": "init",
        "session_id": "s",
    }
    if model is not None:
        record["model"] = model
    return json.dumps(record)


def _subagent(model: str, cache_read: object, message_id: str) -> str:
    record = json.loads(_assistant(cache_read=cache_read, message_id=message_id, model=model))
    record["subagent_type"] = "Explore"
    record["parent_tool_use_id"] = "call_1"
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
    assert usage["peak_context"] == _observed(50000)


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
    assert usage["peak_context"] == _observed(42000)
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
    assert usage["peak_context"] == _observed(80000)
    assert usage["turn_count"] == 2


def test_extract_peak_context_no_assistant_records():
    stdout = _build_ndjson([_result(cache_read=100000)])
    usage, _rows = extract_token_usage(stdout)
    assert usage is not None
    assert usage["peak_context"] == _UNKNOWN
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
    assert usage["peak_context"] == _observed(0)
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
    assert usage["input_tokens"] == _observed(15)
    assert usage["output_tokens"] == _observed(150)
    assert usage["cache_read_tokens"] == _observed(1_500)
    assert usage["cache_write_tokens"] == _observed(15)
    assert usage["peak_context"] == _observed(500)
    assert usage["model_breakdown"]["claude-opus-4-6"]["input_tokens"] == _observed(6)
    assert usage["model_breakdown"]["claude-sonnet-4-6"]["input_tokens"] == _observed(9)
    assert [row["message_id"] for row in rows] == [f"msg-{index}" for index in range(1, 6)]


@pytest.mark.parametrize(
    ("init_models", "parent_models", "model_usage", "include_subagent", "window"),
    [
        pytest.param(
            ("claude-sonnet-5",),
            ("MiniMax-M3",),
            {"claude-sonnet-5": {"contextWindow": 200_000}},
            False,
            200_000,
            id="gateway-unsuffixed",
        ),
        pytest.param(
            ("claude-sonnet-5[1m]",),
            ("MiniMax-M3",),
            {"claude-sonnet-5[1m]": {"contextWindow": 1_000_000}},
            False,
            1_000_000,
            id="gateway-suffixed",
        ),
        pytest.param(
            ("claude-opus-4-6[1m]",),
            ("MiniMax-M3",),
            {
                "claude-opus-4-6[1m]": {"contextWindow": 1_000_000},
                "MiniMax-M3": {"contextWindow": 200_000},
            },
            True,
            1_000_000,
            id="subagent-served-name-collision",
        ),
        pytest.param(
            ("claude-sonnet-5",),
            ("claude-sonnet-5",),
            {"claude-sonnet-5": {"contextWindow": 1_000_000}},
            False,
            1_000_000,
            id="first-party-bare-name",
        ),
        pytest.param(
            ("haiku",),
            ("claude-haiku-4-5-20251001",),
            {"claude-haiku-4-5-20251001": {"contextWindow": 200_000}},
            False,
            200_000,
            id="alias-launch",
        ),
        pytest.param(
            ("claude-sonnet-5[1m]",) * 3,
            ("MiniMax-M3",),
            {"claude-sonnet-5[1m]": {"contextWindow": 1_000_000}},
            False,
            1_000_000,
            id="repeated-init-same-model",
        ),
    ],
)
def test_parent_turn_window_is_the_requested_models_window(
    init_models: tuple[str, ...],
    parent_models: tuple[str, ...],
    model_usage: dict[str, dict[str, object]],
    include_subagent: bool,
    window: int,
):
    lines = [_init(model) for model in init_models]
    lines.extend(
        _assistant(cache_read=100, message_id=f"parent-{index}", model=model)
        for index, model in enumerate(parent_models, start=1)
    )
    if include_subagent:
        lines.append(_subagent("MiniMax-M3", 100, "subagent-1"))
    lines.append(_model_usage_result(model_usage))

    _usage, rows = extract_token_usage(_build_ndjson(lines))

    assert [row["context_window_tokens"] for row in rows] == [window] * len(parent_models)


@pytest.mark.parametrize(
    ("init_model", "model_usage", "include_subagent"),
    [
        pytest.param(
            None,
            {
                "claude-opus-4-6[1m]": {"contextWindow": 1_000_000},
                "MiniMax-M3": {"contextWindow": 200_000},
            },
            False,
            id="missing-init",
        ),
        pytest.param(
            "claude-sonnet-5",
            {
                "MiniMax-M3": {"contextWindow": 200_000},
                "claude-haiku-4-5": {"contextWindow": 400_000},
            },
            False,
            id="requested-model-not-in-usage",
        ),
        pytest.param(
            "claude-sonnet-5",
            {
                "claude-sonnet-5": {"contextWindow": 0},
                "MiniMax-M3": {"contextWindow": 200_000},
            },
            True,
            id="invalid-requested-window-with-valid-subagent-window",
        ),
    ],
)
def test_parent_turn_window_is_unknown_when_the_requested_model_is_ambiguous(
    init_model: str | None,
    model_usage: dict[str, dict[str, object]],
    include_subagent: bool,
):
    lines = [] if init_model is None else [_init(init_model)]
    lines.append(_assistant(cache_read=100, message_id="parent-1", model="MiniMax-M3"))
    if include_subagent:
        lines.append(_subagent("MiniMax-M3", 100, "subagent-1"))
    lines.append(_model_usage_result(model_usage))

    _usage, rows = extract_token_usage(_build_ndjson(lines))

    assert len(rows) == 1
    assert rows[0]["context_window_tokens"] is None
    assert rows[0]["context_fraction"] is None


def test_rows_use_requested_model_window_and_inclusive_input():
    opus = "claude-opus-4-6-20260901"
    sonnet = "claude-sonnet-4-6-20260901"
    requested_model = "claude-sonnet-5"
    stdout = _build_ndjson(
        [
            _init(requested_model),
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
                    requested_model: {"contextWindow": 200},
                }
            ),
        ]
    )

    usage, rows = extract_token_usage(stdout)

    assert usage is not None
    assert usage["input_tokens"] == _observed(30)
    assert [row["backend"] for row in rows] == ["claude-code", "claude-code"]
    assert [row["model"] for row in rows] == [opus, sonnet]
    assert [row["input_tokens"] for row in rows] == [115, 125]
    assert [row["context_window_tokens"] for row in rows] == [200, 200]
    assert [row["context_fraction"] for row in rows] == [0.5, 0.5]


@pytest.mark.parametrize("window", [0, -1, True, 200.0, "200"])
def test_unknown_or_invalid_context_window_leaves_fraction_unknown(
    window: object,
):
    model = "claude-sonnet-4-6"
    stdout = _build_ndjson(
        [
            _init(model),
            _assistant(cache_read=100, message_id="message-1"),
            _model_usage_result({model: {"contextWindow": window}}),
        ]
    )

    _usage, rows = extract_token_usage(stdout)

    assert rows[0]["context_window_tokens"] is None
    assert rows[0]["context_fraction"] is None


def test_conflicting_windows_for_the_requested_model_leave_window_unknown():
    opus = "claude-opus-4-6"
    sonnet = "claude-sonnet-4-6"
    requested_model = "claude-sonnet-5"
    stdout = _build_ndjson(
        [
            _init(requested_model),
            _assistant(cache_read=100, message_id="opus-message", model=opus),
            _assistant(cache_read=100, message_id="sonnet-message", model=sonnet),
            _model_usage_result({requested_model: {"contextWindow": 200}}),
            _model_usage_result({requested_model: {"contextWindow": 300}}),
        ]
    )

    _usage, rows = extract_token_usage(stdout)

    assert [row["context_window_tokens"] for row in rows] == [None, None]
    assert [row["context_fraction"] for row in rows] == [None, None]
