"""Pure row projection tests for the derived report index."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from autoskillit.execution._report_index_rows import (
    normalize_report_row,
    resolve_token_measure,
    rows_for_walk_item,
)
from autoskillit.execution._report_index_types import REPORT_ROW_TYPES, ReportSessionRow
from autoskillit.execution.evidence.report_walk import WalkItem
from tests.execution._report_index_fixtures import otlp_log_record as _log

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


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
    child_outcomes: tuple[dict[str, Any], ...] = (),
    turn_usage_rows: tuple[dict[str, Any], ...] = (),
    turn_usage_state: str = "unavailable",
    turn_usage_reason: str | None = "descriptor-missing",
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
            "child_outcomes": child_outcomes,
            "transcripts_available": available,
            "transcript_unavailable_reasons": [],
            "turn_usage_rows": list(turn_usage_rows),
            "turn_usage_state": turn_usage_state,
            "turn_usage_reason": turn_usage_reason,
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


def test_turn_rows_keep_owner_and_ordinal_identity_with_measure_states() -> None:
    turn = {
        "backend": "codex",
        "provider_used": "openai",
        "message_id": "message-1",
        "request_id": "request-1",
        "timestamp": None,
        "model": "gpt-resolved",
        "input_tokens": {"state": "measured_zero", "value": 0},
        "output_tokens": {"state": "unknown", "value": None},
        "cache_read_tokens": {"state": "measured", "value": 12},
        "cache_write_tokens": {"state": "unavailable", "value": None},
        "context_window_tokens": 100,
        "context_fraction": 0.12,
    }
    first, second = (
        rows_for_walk_item(
            _session_item(
                owner,
                {
                    "session_id": "reused-id",
                    "backend": "codex",
                    "provider_used": "openai",
                },
                turn_usage_rows=(turn,),
                turn_usage_state="observed",
                turn_usage_reason=None,
            )
        )
        for owner in ("attempt-a", "attempt-b")
    )

    assert first[0]["turn_usage_state"] == "observed"
    assert first[0]["turn_usage_reason"] is None
    first_turn, second_turn = first[1], second[1]
    assert first_turn["kind"] == "turn"
    assert first_turn["source_id"] == first_turn["session_key"] == "attempt-a"
    assert second_turn["source_id"] == second_turn["session_key"] == "attempt-b"
    assert first_turn["key"] != second_turn["key"]
    assert first_turn["ordinal"] == second_turn["ordinal"] == 0
    assert first_turn["session_id"] == "reused-id"
    assert first_turn["time_ms"] is None
    assert first_turn["request_id"] == "request-1"
    assert first_turn["message_id"] == "message-1"
    assert first_turn["harness"] == "codex"
    assert first_turn["provider"] == "openai"
    assert first_turn["model"] == "gpt-resolved"
    assert first_turn["input_tokens"] == {"state": "measured_zero", "value": 0}
    assert first_turn["output_tokens"] == {"state": "unknown", "value": None}
    assert first_turn["context_window_tokens"] == 100
    assert first_turn["context_fraction"] == 0.12
    assert set(first_turn) == set(REPORT_ROW_TYPES["turn"].__annotations__)


def test_session_row_keeps_missing_turn_ledger_coverage() -> None:
    (row,) = rows_for_walk_item(
        _session_item(
            "no-ledger",
            {
                "session_id": "sid",
                "backend": "claude-code",
                "provider_used": "anthropic",
            },
            turn_usage_state="unavailable",
            turn_usage_reason="ledger-not-published",
        )
    )

    assert row["turn_usage_state"] == "unavailable"
    assert row["turn_usage_reason"] == "ledger-not-published"


def test_turn_measure_decoders_preserve_unknown_for_invalid_shapes() -> None:
    turn = {
        "backend": "codex",
        "provider_used": "openai",
        "message_id": None,
        "request_id": None,
        "timestamp": None,
        "model": "gpt-resolved",
        "input_tokens": {"state": "future-state", "value": 9},
        "output_tokens": {"state": "measured", "value": 4},
        "cache_read_tokens": {"state": "unknown", "value": None},
        "cache_write_tokens": {"state": "unavailable", "value": None},
        "context_window_tokens": None,
        "context_fraction": None,
    }
    _, row = rows_for_walk_item(
        _session_item(
            "attempt",
            {"session_id": "sid"},
            turn_usage_rows=(turn,),
            turn_usage_state="observed",
            turn_usage_reason=None,
        )
    )

    assert row["input_tokens"] == {"state": "unknown", "value": None}
    invalid_serialized = normalize_report_row(
        {**row, "input_tokens": {"state": "measured", "value": True}}
    )
    assert invalid_serialized is not None
    assert invalid_serialized["input_tokens"] == {"state": "unknown", "value": None}


@pytest.mark.parametrize("session_type", ["skill", "orchestrator"])
def test_session_item_projects_verified_native_child_under_index_parent_key(
    session_type: str,
) -> None:
    token_usage = {
        "input_tokens": {"state": "measured", "value": 15},
        "output_tokens": {"state": "measured", "value": 4},
        "cache_read_tokens": {"state": "measured", "value": 3},
        "cache_write_tokens": {"state": "measured_zero", "value": 0},
    }
    child = {
        "child_id": "native-child-1",
        "backend": "claude_code",
        "parent_session_id": "native-parent-id",
        "role": "audit-impl-slice-auditor",
        "attribution_skill": "make-plan",
        "effective_provider": "anthropic",
        "effective_model": "claude-sonnet-child",
        "evidence_source": "transcript_metadata",
        "transcript_locator": "/private/logs/agent-native-child-1.jsonl",
        "token_usage": token_usage,
        "tool_counts": {"Bash": 1, "Read": 2},
        "transcript_state": "observed",
        "usage_state": "observed",
    }
    managed_attempt = {
        "child_id": "managed-attempt-1",
        "launch_alias": "claude-managed-attempt",
        "backend": "claude_code",
        "parent_session_id": "native-parent-id",
        "role": "audit-bugs",
        "attribution_skill": "make-plan",
        "evidence_source": "managed_attempt_reservation",
    }
    providerless_child = {
        "child_id": "native-child-providerless",
        "backend": "claude_code",
        "parent_session_id": "native-parent-id",
        "role": "audit-impl-slice-auditor",
        "attribution_skill": "make-plan",
        "effective_provider": "",
        "effective_model": "",
        "evidence_source": "transcript_metadata",
        "transcript_locator": "/private/logs/agent-native-child-providerless.jsonl",
        "token_usage": {
            field: {"state": "unknown", "value": None}
            for field in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            )
        },
        "tool_counts": {"Read": 1},
        "transcript_state": "observed",
        "usage_state": "unknown",
    }
    mismatched_skill_child = {
        "child_id": "native-child-mismatched-skill",
        "backend": "claude_code",
        "parent_session_id": "native-parent-id",
        "role": "audit-impl-slice-auditor",
        "attribution_skill": "other-skill",
        "effective_provider": "anthropic",
        "effective_model": "claude-sonnet-child",
        "evidence_source": "transcript_metadata",
        "token_usage": token_usage,
        "tool_counts": {},
        "transcript_state": "observed",
        "usage_state": "observed",
    }
    parent = {
        "session_id": "native-parent-id",
        "timestamp": "2026-09-01T00:00:00Z",
        "backend": "claude-code",
        "provider_used": "openai",
        "model_identifier": "parent-model",
        "skill_command": "/autoskillit:make-plan",
        "recipe_name": "research",
        "step_name": "scope",
        "session_type": session_type,
    }

    rows = rows_for_walk_item(
        _session_item(
            "report-index-key",
            parent,
            child_outcomes=(child, managed_attempt, providerless_child, mismatched_skill_child),
        )
    )

    assert [row["kind"] for row in rows] == [
        "session",
        "subagent",
        "subagent",
        "subagent",
    ]
    projected_by_id = {row["child_id"]: row for row in rows if row["kind"] == "subagent"}
    projected = projected_by_id["native-child-1"]
    assert projected["key"].startswith("report-index-key:")
    assert projected["child_id"] == "native-child-1"
    assert projected["parent_session_key"] == "report-index-key"
    assert projected["native_parent_session_id"] == "native-parent-id"
    assert projected["role"] == "audit-impl-slice-auditor"
    assert projected["actor_level"] == "L0"
    assert projected["skill"] == "make-plan"
    assert projected["level"] == session_type
    assert projected["recipe"] == "research"
    assert projected["step"] == "scope"
    assert projected["provider"] == "anthropic"
    assert projected["model"] == "claude-sonnet-child"
    assert projected["time_ms"] == rows[0]["time_ms"]
    assert projected["token_usage"] == token_usage
    assert projected["tool_counts"] == {"Bash": 1, "Read": 2}
    assert projected["transcript_state"] == projected["usage_state"] == "observed"
    without_provider = projected_by_id["native-child-providerless"]
    assert without_provider["provider"] == "unknown"
    assert without_provider["model"] is None
    assert without_provider["token_usage"]["input_tokens"]["state"] == "unknown"
    assert without_provider["usage_state"] == "unknown"
    mismatched = projected_by_id["native-child-mismatched-skill"]
    assert mismatched["skill"] is None
    assert mismatched["role"] == "audit-impl-slice-auditor"
    assert mismatched["level"] == session_type


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
    spans = subagent["parent_context_spans"]
    assert [span["field"] for span in spans] == [
        "parent_prompt_tokens",
        "subagent_return_tokens",
    ]
    assert all(span["measure"] == {"state": "unavailable", "value": None} for span in spans)
    assert all(span["reason"] == "parent_invocation_unverified" for span in spans)
    assert all(span["source_id"] == f"{source_id}#4" for span in spans)
    assert all(span["invocation_id"] is None and span["turn_id"] is None for span in spans)
    assert all(span["harness"] == "claude-code" for span in spans)
    assert all(span["provider"] is None and span["model"] is None for span in spans)
    assert all(
        span["timestamp"] is None
        and span["tokenizer_version"] is None
        and span["encoding"] is None
        for span in spans
    )
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
    # The fixture file ``claude_native_token_evidence_v2_1_257.json`` is a
    # captured native OTLP log payload from a real Claude Code session
    # (recorded upstream in #5098). The versioned suffix encodes the
    # Claude Code release it was sampled from; bump the file (and update the
    # expected values below) when a release changes the emission shape.
    fixture_path = _FIXTURE_DIR / "claude_native_token_evidence_v2_1_257.json"
    if not fixture_path.is_file():
        pytest.fail(
            f"Missing native OTLP fixture {fixture_path}. The fixture is recorded "
            'upstream in PR #5098 ("feat: preserve provider token availability '
            'across reports"); re-record from a real Claude Code session when '
            "the file is gone or when bumping the versioned suffix."
        )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))["payload"]
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
