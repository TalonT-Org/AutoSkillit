"""Frozen report-analysis packet contracts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

import pytest

from autoskillit.execution import REPORT_INDEX_SCHEMA_VERSION
from autoskillit.report.deck._analysis import _json_bytes, prepare_run_analysis
from tests.report._fixtures import session_row, token_measure

pytestmark = [pytest.mark.small]

_GUARDRAIL = "Observed differences prompt investigation; they are not proof of causation."
_MEASURE_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def _session(
    key: str,
    *,
    time_ms: int | bool,
    timestamp: str = "2026-09-30T00:00:00Z",
    rollout: object = None,
    input_tokens: dict[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    return session_row(
        key,
        schema_version=REPORT_INDEX_SCHEMA_VERSION,
        session_id=f"native-{key}",
        time_ms=time_ms,
        timestamp=timestamp,
        rollout=rollout,
        success=True,
        harness="claude-code",
        provider="anthropic",
        model="model-a",
        skill="make-plan",
        recipe="recipe-a",
        step="scope",
        level="skill",
        input_tokens=input_tokens if input_tokens is not None else token_measure(None),
        **fields,
    )


def _populations() -> dict[str, list[dict[str, Any]]]:
    sessions = [_session("session-a", time_ms=100), _session("session-b", time_ms=200)]
    requests = [
        {
            "schema_version": REPORT_INDEX_SCHEMA_VERSION,
            "kind": "request",
            "key": f"{session['key']}:request-{index}",
            "session_id": session["session_id"],
            "time_ms": session["time_ms"] + 10,
            "harness": "claude-code",
            "request_id": f"request-{index}",
            "model": "model-a",
            "input_tokens": 10 + index,
            "output_tokens": 2,
        }
        for index, session in enumerate(sessions)
    ]
    tools = [
        {
            "schema_version": REPORT_INDEX_SCHEMA_VERSION,
            "kind": "tool",
            "key": f"{session['key']}:tool-{index}",
            "session_id": session["session_id"],
            "time_ms": session["time_ms"] + 20,
            "harness": "claude-code",
            "tool_name": "Task",
            "tool_use_id": f"tool-{index}",
            "success": True,
        }
        for index, session in enumerate(sessions)
    ]
    subagents = [
        {
            "schema_version": REPORT_INDEX_SCHEMA_VERSION,
            "kind": "subagent",
            "key": f"{session['key']}:child-{index}",
            "session_id": session["session_id"],
            "time_ms": session["time_ms"] + 30,
            "harness": "claude-code",
            "child_id": f"child-{index}",
            "native_parent_session_id": session["session_id"],
            "parent_session_key": session["key"],
            "role": "planner",
            "provider": "anthropic",
            "model": "child-model",
            "skill": "make-plan",
            "recipe": "recipe-a",
            "step": "scope",
            "token_usage": {field: token_measure(1) for field in _MEASURE_FIELDS},
            "tool_counts": {},
            "transcript_state": "observed",
            "usage_state": "observed",
            "parent_context_spans": [],
        }
        for index, session in enumerate(sessions)
    ]
    turns = [
        {
            "schema_version": REPORT_INDEX_SCHEMA_VERSION,
            "kind": "turn",
            "key": f"{session['key']}:turn:0",
            "session_id": session["session_id"],
            "session_key": session["key"],
            "source_id": session["key"],
            "time_ms": session["time_ms"] + 40,
            "ordinal": 0,
            "request_id": None,
            "message_id": f"message-{index}",
            "harness": "claude-code",
            "provider": "anthropic",
            "model": "model-a",
            "skill": "make-plan",
            "recipe": "recipe-a",
            "step": "scope",
            **{field: token_measure(1) for field in _MEASURE_FIELDS},
            "context_window_tokens": 100,
            "context_fraction": 0.5,
        }
        for index, session in enumerate(sessions)
    ]
    return {
        "sessions": sessions,
        "requests": requests,
        "tools": tools,
        "subagents": subagents,
        "turns": turns,
    }


def _prepare(
    populations: Mapping[str, list[dict[str, Any]]] | None = None,
    *,
    change_id: str = "",
    render_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = populations or {}
    return prepare_run_analysis(
        rows.get("sessions", ()),
        request_rows=rows.get("requests", ()),
        tool_rows=rows.get("tools", ()),
        subagent_rows=rows.get("subagents", ()),
        turn_rows=rows.get("turns", ()),
        index_schema_version=REPORT_INDEX_SCHEMA_VERSION,
        change_id=change_id,
        render_bundle=render_bundle or {"html": "<!doctype html>"},
    )


def _rollout(
    change_id: str = "change-a", *, effective_at: str = "2026-10-01T00:00:00Z"
) -> dict[str, Any]:
    return {
        "change_id": change_id,
        "effective_at": effective_at,
        "scope": {"skills": ["make-plan"], "backends": ["claude-code"], "roles": ["planner"]},
        "expected_effect": "Reduce input tokens while preserving requirement coverage.",
    }


@pytest.mark.parametrize(
    "population_name", ("sessions", "requests", "tools", "subagents", "turns")
)
def test_input_fingerprint_is_independent_of_each_population_order(population_name: str) -> None:
    populations = _populations()
    expected = _prepare(populations)
    permuted = {name: list(rows) for name, rows in populations.items()}
    permuted[population_name].reverse()

    actual = _prepare(permuted)

    assert actual["input_fingerprint"] == expected["input_fingerprint"]
    assert _json_bytes(actual) == _json_bytes(expected)


def test_empty_change_id_has_no_cohort_comparisons() -> None:
    cohort = _prepare({"sessions": [_session("session-a", time_ms=1)]})["cohort"]

    assert cohort == {
        "change_id": None,
        "reason": "No rollout cohort applied.",
        "comparisons": [],
    }


@pytest.mark.parametrize(
    "population_name", ("sessions", "requests", "tools", "subagents", "turns")
)
def test_anchor_includes_integer_times_from_every_population(population_name: str) -> None:
    populations = _populations()
    for rows in populations.values():
        for row in rows:
            row["time_ms"] = 0
    populations[population_name][0]["time_ms"] = 987_654
    populations["sessions"].append(_session("bool-time", time_ms=True))
    populations["requests"][1]["time_ms"] = 987_655.5

    packet = _prepare(populations)

    assert packet["payload"]["generated_at_ms"] == 987_654


@pytest.mark.parametrize("case", ("epoch-zero", "empty-snapshot", "boolean-time-is-ignored"))
def test_anchor_uses_zero_for_epoch_empty_and_boolean_only_times(case: str) -> None:
    if case == "epoch-zero":
        populations = {"sessions": [_session("epoch", time_ms=0)]}
    elif case == "boolean-time-is-ignored":
        populations = {"sessions": [_session("boolean", time_ms=True)]}
    else:
        populations = {}

    packet = _prepare(populations)

    assert packet["payload"]["generated_at_ms"] == 0


def test_cohort_compares_full_aware_timestamps_and_places_cutover_on_after_side() -> None:
    marker = _rollout(effective_at="2026-09-30T23:59:59.999500Z")
    rows = [
        _session(
            "before",
            time_ms=1,
            timestamp="2026-10-01T01:59:59.999400+02:00",
            rollout=marker,
        ),
        _session(
            "at-cutover",
            time_ms=1,
            timestamp="2026-09-30T23:59:59.999500Z",
            rollout=marker,
        ),
        _session(
            "after",
            time_ms=1,
            timestamp="2026-09-30T23:59:59.999600Z",
            rollout=marker,
        ),
    ]

    cohort = _prepare({"sessions": rows}, change_id="change-a")["cohort"]

    assert cohort["before_count"] == 1
    assert cohort["after_count"] == 2
    assert cohort["excluded_count"] == 0


def test_cohort_summaries_keep_zero_unknown_empty_side_and_guardrail() -> None:
    marker = _rollout()
    rows = [
        _session(
            "zero",
            time_ms=1,
            timestamp="2026-09-29T00:00:00Z",
            rollout=marker,
            input_tokens=token_measure(0),
        ),
        _session(
            "unknown",
            time_ms=2,
            timestamp="2026-09-30T00:00:00Z",
            rollout=marker,
            input_tokens=token_measure(None),
        ),
        _session("excluded", time_ms=3),
    ]

    cohort = _prepare({"sessions": rows}, change_id="change-a")["cohort"]
    comparison = next(item for item in cohort["comparisons"] if item["metric"] == "input_tokens")
    before, after, delta = comparison["before"], comparison["after"], comparison["delta"]

    assert (cohort["before_count"], cohort["after_count"], cohort["excluded_count"]) == (2, 0, 1)
    assert before["eligible_sessions"] == 2
    assert before["observed_sessions"] == 1
    assert before["value"] == 0.0
    assert before["state_counts"]["measured_zero"] == 1
    assert before["state_counts"]["unknown"] == 1
    assert after["state"] == "no_observations"
    assert after["eligible_sessions"] == 0
    assert after["observed_sessions"] == 0
    assert delta["state"] == "no_observations"
    assert delta["reason"] == "later interval has no observed values"
    assert delta["guardrail"] == _GUARDRAIL
    assert before["unit"] == after["unit"] == delta["unit"] == "tokens/session"
    failure = next(item for item in cohort["comparisons"] if item["metric"] == "failure_share")
    assert failure["before"]["unit"] == failure["after"]["unit"] == "ratio"
    assert failure["delta"]["unit"] == "percentage_points"


def test_fractional_cohort_averages_survive_canonical_json_and_fingerprint() -> None:
    marker = _rollout()
    rows = [
        _session(
            "before-1",
            time_ms=1,
            timestamp="2026-09-29T00:00:00Z",
            rollout=marker,
            input_tokens=token_measure(1),
        ),
        _session(
            "before-2",
            time_ms=2,
            timestamp="2026-09-30T00:00:00Z",
            rollout=marker,
            input_tokens=token_measure(2),
        ),
        _session(
            "after-1",
            time_ms=3,
            timestamp="2026-10-02T00:00:00Z",
            rollout=marker,
            input_tokens=token_measure(4),
        ),
        _session(
            "after-2",
            time_ms=4,
            timestamp="2026-10-03T00:00:00Z",
            rollout=marker,
            input_tokens=token_measure(5),
        ),
    ]
    packet = _prepare(
        {"sessions": rows},
        change_id="change-a",
        render_bundle={"label": "Résumé", "html": "<html>"},
    )
    serialized = _json_bytes(packet)
    decoded = json.loads(serialized)
    comparison = next(
        item for item in decoded["cohort"]["comparisons"] if item["metric"] == "input_tokens"
    )
    fingerprint_input = dict(packet)
    fingerprint_input.pop("input_fingerprint")

    assert comparison["before"]["value"] == 1.5
    assert comparison["after"]["value"] == 4.5
    assert comparison["delta"]["value"] == 3.0
    assert isinstance(comparison["before"]["value"], float)
    assert math.isfinite(comparison["before"]["value"])
    assert (
        packet["input_fingerprint"] == hashlib.sha256(_json_bytes(fingerprint_input)).hexdigest()
    )
    assert _json_bytes({"z": 1.5, "a": 0.0}) == b'{"a":0.0,"z":1.5}'


@pytest.mark.parametrize("rollout", (7, ["change-a"]))
def test_non_object_rollout_evidence_fails_closed(rollout: object) -> None:
    with pytest.raises(ValueError, match="rollout evidence"):
        _prepare(
            {"sessions": [_session("bad-marker", time_ms=1, rollout=rollout)]},
            change_id="change-a",
        )


def test_matched_rollout_marker_must_be_valid() -> None:
    with pytest.raises(ValueError):
        _prepare(
            {"sessions": [_session("bad-marker", time_ms=1, rollout={"change_id": "change-a"})]},
            change_id="change-a",
        )


@pytest.mark.parametrize("timestamp", (None, "not-a-date", "2026-09-30T00:00:00"))
def test_matched_rollout_timestamp_must_be_aware_iso8601(timestamp: str | None) -> None:
    with pytest.raises(ValueError, match="aware ISO-8601"):
        _prepare(
            {
                "sessions": [
                    _session("bad-timestamp", time_ms=1, timestamp=timestamp, rollout=_rollout())
                ]
            },
            change_id="change-a",
        )


def test_conflicting_rollout_declarations_for_one_change_id_fail() -> None:
    first = _rollout()
    second = {**first, "expected_effect": "A conflicting prediction."}

    with pytest.raises(ValueError, match="conflicting rollout declarations"):
        _prepare(
            {
                "sessions": [
                    _session("first", time_ms=1, rollout=first),
                    _session("second", time_ms=2, rollout=second),
                ]
            },
            change_id="change-a",
        )


def test_unknown_change_id_fails_instead_of_returning_empty_cohort() -> None:
    with pytest.raises(ValueError, match="no retained runs match"):
        _prepare(
            {"sessions": [_session("other", time_ms=1, rollout=_rollout("other-change"))]},
            change_id="change-a",
        )
