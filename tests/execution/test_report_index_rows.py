"""Pure row projection tests for the derived report index."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from autoskillit.execution._report_index_rows import (
    REPORT_ROW_TYPES,
    ReportSessionRow,
    normalize_report_row,
    resolve_token_measure,
    rows_for_walk_item,
)
from autoskillit.execution.evidence.report_walk import WalkItem

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _attrs(**values: object) -> list[dict[str, Any]]:
    attributes = []
    for key, value in values.items():
        if isinstance(value, bool):
            encoded = {"boolValue": value}
        elif isinstance(value, str):
            encoded = {"stringValue": value}
        elif isinstance(value, int):
            encoded = {"intValue": value}
        elif isinstance(value, float):
            encoded = {"doubleValue": value}
        else:
            raise TypeError(f"Unsupported test attribute value: {type(value).__name__}")
        attributes.append({"key": key, "value": encoded})
    return attributes


def _log(
    event: str,
    session_id: str | None,
    *,
    time_ns: int | None = None,
    observed_ns: int | None = None,
    **attrs: object,
) -> dict[str, Any]:
    attributes = {"event.name": event, **attrs}
    if session_id is not None:
        attributes["session.id"] = session_id
    record: dict[str, Any] = {"attributes": _attrs(**attributes)}
    if time_ns is not None:
        record["timeUnixNano"] = str(time_ns)
    if observed_ns is not None:
        record["observedTimeUnixNano"] = str(observed_ns)
    return record


def _otlp_item(source_id: str, *records: dict[str, Any], signal: str = "logs") -> WalkItem:
    return WalkItem(
        "otlp",
        source_id,
        None,
        {
            "record_id": source_id,
            "signal": signal,
            "payload": {
                "resourceLogs": [
                    {
                        "scopeLogs": [
                            {
                                "scope": {"name": "com.anthropic.claude_code.events"},
                                "logRecords": list(records),
                            }
                        ]
                    }
                ]
            },
        },
        {},
    )


def _session_item(
    dir_name: str,
    row: dict[str, Any],
    *,
    turns: tuple[tuple[str, ...], ...] = (),
    available: bool = True,
) -> WalkItem:
    return WalkItem(
        "session",
        dir_name,
        row.get("session_id"),
        {
            "row": row,
            "assistant_turn_count": len(turns) if available else None,
            "assistant_turns": [
                {"turn_id": "private-turn-id", "timestamp": None, "tool_names": list(names)}
                for names in turns
            ],
            "transcripts_available": available,
            "transcript_unavailable_reasons": [],
        },
        {},
    )


def test_session_row_carries_facets_pair_and_resolved_measures() -> None:
    timestamp = "2026-09-01T00:00:00.500000+00:00"
    source_row = {
        "schema_version": 14,
        "session_id": "sid-session",
        "backend": "claude-code",
        "provider_used": "anthropic",
        "skill_command": "/autoskillit:make-plan x",
        "step_name": "scope",
        "recipe_name": "research",
        "kitchen_id": "kitchen-1",
        "order_id": "order-1",
        "session_type": "skill",
        "success": False,
        "subtype": "context_exhausted",
        "adjudication_verdict": {
            "reason_kind": "resume",
            "subtype": "context_exhausted",
            "detail": "d",
            "outcome_fields": None,
            "defects": [],
        },
        "timestamp": timestamp,
        "duration_seconds": 1.25,
        "model_identifier": "resolved-model",
        "configured_model": "alias",
        "effective_parent_model": "parent-model",
        "cli_subtype": "other",
        "cwd": "/private/workspace/project",
        "claude_code_log": "/private/transcripts/session.jsonl",
        "input_tokens": {"state": "measured", "value": 10},
        "output_tokens": {"state": "measured", "value": 20},
        "cache_read_tokens": {"state": "measured_zero", "value": 0},
        "cache_write_tokens": {"state": "measured", "value": 30},
    }
    item = _session_item(
        "session-dir",
        source_row,
        turns=(("Read", "Bash"), ("Read",)),
    )

    (row,) = rows_for_walk_item(item)
    expected_time_ms = (
        datetime.fromisoformat(timestamp) - datetime(1970, 1, 1, tzinfo=UTC)
    ) // timedelta(milliseconds=1)

    assert row["harness"] == "claude-code"
    assert row["provider"] == "anthropic"
    assert row["skill"] == "make-plan"
    assert row["recipe"] == "research"
    assert row["step"] == "scope"
    assert row["level"] == "skill"
    assert row["kitchen_id"] == "kitchen-1"
    assert row["order_id"] == "order-1"
    assert row["success"] is False
    assert row["adjudication_reason"] == "resume"
    assert row["adjudication_subtype"] == "context_exhausted"
    assert row["duration_seconds"] == 1.25
    assert row["model"] == "resolved-model"
    assert row["subtype"] == "context_exhausted"
    assert row["time_ms"] == expected_time_ms == 1788220800500
    assert row["tool_counts"] == {"Bash": 1, "Read": 2}
    assert row["assistant_turn_count"] == 2
    assert row["input_tokens"] == source_row["input_tokens"]
    assert row["output_tokens"] == source_row["output_tokens"]
    assert row["cache_read_tokens"] == source_row["cache_read_tokens"]
    assert row["cache_write_tokens"] == source_row["cache_write_tokens"]
    assert set(row) == set(ReportSessionRow.__annotations__)
    serialized = json.dumps(row)
    assert "cwd" not in serialized
    assert "/private/workspace/project" not in serialized
    assert "/private/transcripts/session.jsonl" not in serialized
    assert "private-turn-id" not in serialized


def test_session_row_legacy_zero_and_codex_sigil() -> None:
    row_data = {
        "schema_version": 13,
        "session_id": "sid-codex",
        "backend": "codex",
        "provider_used": "codex",
        "skill_command": "$autoskillit:make-plan x",
        "cache_write_tokens": 0,
        "output_tokens": 7,
    }
    (codex_row,) = rows_for_walk_item(_session_item("codex-dir", row_data))

    assert codex_row["skill"] == "make-plan"
    assert codex_row["cache_write_tokens"] == {"state": "unavailable", "value": None}
    assert codex_row["output_tokens"] == {"state": "measured", "value": 7}

    claude_row_data = {**row_data, "backend": "claude-code", "provider_used": "anthropic"}
    (claude_row,) = rows_for_walk_item(_session_item("claude-dir", claude_row_data))
    assert claude_row["cache_write_tokens"] == {"state": "unknown", "value": None}

    (unknown_pair_row,) = rows_for_walk_item(
        _session_item("unknown-dir", {"session_id": "sid-unknown"})
    )
    assert unknown_pair_row["harness"] == "unknown"
    assert unknown_pair_row["provider"] == "unknown"

    unavailable = _session_item(
        "unavailable-dir",
        {"session_id": "sid-unavailable"},
        available=False,
    )
    (unavailable_row,) = rows_for_walk_item(unavailable)
    assert unavailable_row["tool_counts"] is None
    assert unavailable_row["assistant_turn_count"] is None


def test_otlp_item_projects_request_tool_and_subagent_rows() -> None:
    sid = "sid-otlp"
    source_id = "record-otlp"
    time_ns = 1_778_220_800_500_000_000
    observed_ns = 1_778_220_801_000_000_000
    item = _otlp_item(
        source_id,
        _log(
            "api_request",
            sid,
            request_id="request-1",
            input_tokens=3,
            output_tokens=5,
            cache_read_tokens=0,
            cost_usd=0.25,
            duration_ms=900,
            model="claude-model",
            query_source="sdk",
            **{"event.sequence": 4},
            time_ns=time_ns,
        ),
        _log(
            "api_request",
            sid,
            observed_ns=observed_ns,
            request_id="request-2",
            **{"agent.name": "Explore", "cacheCreation": 11},
        ),
        _log(
            "tool_result",
            sid,
            tool_use_id="tool-1",
            tool_name="Bash",
            success="true",
            duration_ms=12,
            tool_result_size_bytes=42,
            error_type="",
        ),
        _log("tool_result", sid, tool_name="Read", success="false"),
        _log(
            "subagent_completed",
            sid,
            agent_type="Explore",
            model="child-model",
            final_model="final-model",
            model_swapped=False,
        ),
        _log("api_request", None, request_id="missing-session"),
        _log("api_request", sid),
    )
    rows = rows_for_walk_item(item)
    by_kind = {kind: [row for row in rows if row["kind"] == kind] for kind in REPORT_ROW_TYPES}
    request, subagent_request = by_kind["request"]
    identified_tool, positional_tool = by_kind["tool"]
    subagent = by_kind["subagent"][0]

    assert len(rows) == 5
    assert request["key"] == f"{sid}:request-1"
    assert request["input_tokens"] == 3
    assert request["output_tokens"] == 5
    assert request["cache_read_tokens"] == 0
    assert request["cache_write_tokens"] is None
    assert request["cost_usd"] == 0.25
    assert request["time_ms"] == time_ns // 10**6
    assert subagent_request["key"] == f"{sid}:request-2"
    assert subagent_request["agent_name"] == "Explore"
    assert subagent_request["cache_write_tokens"] == 11
    assert subagent_request["time_ms"] == observed_ns // 10**6
    assert identified_tool["key"] == f"{sid}:tool-1"
    assert identified_tool["success"] is True
    assert positional_tool["key"] == f"{source_id}#3"
    assert subagent["key"] == f"{source_id}#4"
    assert subagent["model_swapped"] is False
    assert rows_for_walk_item(_otlp_item(source_id, _log("api_request", sid))) == []
    assert (
        rows_for_walk_item(
            _otlp_item(
                source_id,
                _log("api_request", sid, request_id="signal-test"),
                signal="metrics",
            )
        )
        == []
    )
    assert rows_for_walk_item(WalkItem("checkpoint", None, None, None, {})) == []
    (untimed_request,) = rows_for_walk_item(
        _otlp_item("record-untimed", _log("api_request", sid, request_id="untimed"))
    )
    assert untimed_request["time_ms"] is None
    for row in rows:
        assert set(row) == set(REPORT_ROW_TYPES[row["kind"]].__annotations__)


def test_resolve_token_measure_is_keyed_on_the_pair() -> None:
    assert resolve_token_measure("claude-code", "anthropic", "cache_write_tokens", None) == {
        "state": "unknown",
        "value": None,
    }
    unavailable = {"state": "unavailable", "value": None}
    assert (
        resolve_token_measure("claude-code", "minimax", "cache_write_tokens", None) == unavailable
    )
    assert (
        resolve_token_measure("claude-code", "MiniMax", "cache_write_tokens", None) == unavailable
    )
    assert resolve_token_measure("claude-code", "anthropic", "cache_write_tokens", 0) == {
        "state": "measured_zero",
        "value": 0,
    }
    assert resolve_token_measure(
        "claude-code",
        "anthropic",
        "cache_write_tokens",
        {"state": "measured", "value": 5, "extra": 1},
    ) == {"state": "unknown", "value": None}
    assert resolve_token_measure("", "", "cache_write_tokens", None) == {
        "state": "unknown",
        "value": None,
    }


def test_normalize_report_row_tolerates_older_and_newer_rows() -> None:
    assert normalize_report_row(None) is None
    assert normalize_report_row({"key": "key"}) is None
    assert normalize_report_row({"kind": "future_kind", "key": "key"}) is None
    assert normalize_report_row({"kind": "session", "key": ""}) is None

    row = normalize_report_row({"kind": "session", "key": "session-dir", "session_id": "sid"})
    assert row is not None
    assert set(row) == set(ReportSessionRow.__annotations__)
    assert row["harness"] == "unknown"
    assert row["provider"] == "unknown"
    for name, value in row.items():
        if name not in {"kind", "key", "session_id", "harness", "provider"}:
            assert value is None

    newer = normalize_report_row(
        {"kind": "session", "key": "session-dir", "schema_version": 2, "future_field": 1}
    )
    assert newer is not None
    assert newer["schema_version"] == 2
    assert "future_field" not in newer

    invalid = normalize_report_row(
        {
            "kind": "session",
            "key": "session-dir",
            "tool_counts": {"Read": 2, "Bad": -1, 3: 1},
            "success": "yes",
        }
    )
    assert invalid is not None
    assert invalid["tool_counts"] == {"Read": 2}
    assert invalid["success"] is None
    invalid_counts = normalize_report_row(
        {"kind": "session", "key": "session-dir", "tool_counts": "x"}
    )
    assert invalid_counts is not None
    assert invalid_counts["tool_counts"] is None


def test_native_claude_capture_projects_one_request_row() -> None:
    payload = json.loads(
        (_FIXTURE_DIR / "claude_native_token_evidence_v2_1_257.json").read_text(encoding="utf-8")
    )["payload"]
    source_id = "rec-native"
    item = WalkItem(
        "otlp",
        source_id,
        None,
        {"record_id": source_id, "signal": "logs", "payload": payload},
        {},
    )

    (row,) = rows_for_walk_item(item)

    assert row["kind"] == "request"
    assert row["key"] == ("00000000-0000-4000-8000-000000000001:req_native_claude_0000001")
    assert row["input_tokens"] == 2
    assert row["output_tokens"] == 4
    assert row["cache_read_tokens"] == 0
    assert row["cache_write_tokens"] == 36520
    assert row["model"] == "claude-fable-5-1"
    assert row["query_source"] == "sdk"
    assert row["event_sequence"] == 65
    assert row["agent_name"] is None
    assert row["time_ms"] is None
    assert row["cost_usd"] is None
    assert row["duration_ms"] is None
