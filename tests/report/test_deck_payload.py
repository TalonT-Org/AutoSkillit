"""Tests for encoding the report deck's index data contract."""

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from autoskillit.core import (
    MeasureRecord,
    SourcePair,
    TokenMeasure,
    TokenMeasureState,
    aggregate_measures,
    measure_ratio,
)
from autoskillit.report.deck import _payload as deck_payload_module
from autoskillit.report.deck import build_deck_payload
from autoskillit.report.deck._payload import encode_table
from autoskillit.report.deck._registry import (
    AVAILABILITY_VOCABULARY,
    DECK_VIEWS,
    LANDING_VIEW,
    SESSION_COLUMNS,
    SESSION_TABLE,
)
from tests.report._fixtures import DECK_GENERATED_AT, subagent_row, token_measure
from tests.report._fixtures import session_row as _row

pytestmark = [pytest.mark.small]
GEN_MS = 1_791_072_000_000


def _payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return build_deck_payload(
        rows,
        request_rows=(),
        tool_rows=(),
        subagent_rows=(),
        generated_at=DECK_GENERATED_AT,
        index_schema_version=7,
    )


def _chip(chips: dict[str, list[dict[str, Any]]], facet: str, key: str) -> dict[str, Any]:
    return next(chip for chip in chips[facet] if chip["key"] == key)


_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
_AUDITOR = "audit-impl-slice-auditor"


def _session_fact(
    key: str,
    *,
    days_ago: int,
    skill: str,
    level: str,
    harness: str = "claude-code",
    provider: str = "anthropic",
    input_tokens: int | None = 10,
    output_tokens: int | None = 2,
    cache_read_tokens: int | None = 0,
    cache_write_tokens: int | None = 0,
) -> dict[str, Any]:
    return _row(
        key,
        session_id=f"native-{key}",
        time_ms=GEN_MS - days_ago * 86_400_000,
        harness=harness,
        provider=provider,
        skill=skill,
        recipe=f"recipe-{skill}",
        step="inspect",
        level=level,
        **{
            field: token_measure(value)
            for field, value in zip(
                _TOKEN_FIELDS,
                (input_tokens, output_tokens, cache_read_tokens, cache_write_tokens),
                strict=True,
            )
        },
    )


def _full_index_facts() -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    sessions = [
        _session_fact("skill", days_ago=1, skill="planner", level="skill", input_tokens=10),
        _session_fact(
            "orchestrator", days_ago=2, skill="planner", level="orchestrator", input_tokens=20
        ),
        _session_fact("review", days_ago=3, skill="review", level="skill", input_tokens=5),
        _session_fact(
            "other-provider",
            days_ago=4,
            skill="planner",
            level="skill",
            provider="minimax",
            input_tokens=7,
        ),
        _session_fact(
            "codex",
            days_ago=9,
            skill="planner",
            level="skill",
            harness="codex",
            provider="codex",
            input_tokens=4,
        ),
        _session_fact("no-child", days_ago=0, skill="maintenance", level="skill", input_tokens=0),
    ]
    by_key = {row["key"]: row for row in sessions}
    children = [
        subagent_row(
            "child-a",
            parent=by_key["skill"],
            role=_AUDITOR,
            skill="planner",
            provider="anthropic",
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=10,
            cache_write_tokens=2,
            tool_counts={"Bash": 3944, "Read": 6},
        ),
        subagent_row(
            "child-b",
            parent=by_key["orchestrator"],
            role=_AUDITOR,
            skill="planner",
            provider="anthropic",
            input_tokens=0,
            output_tokens=10,
            cache_read_tokens=0,
            cache_write_tokens=0,
            tool_counts={"Bash": 1, "Read": 1},
        ),
        subagent_row(
            "child-c",
            parent=by_key["review"],
            role=_AUDITOR,
            skill="review",
            provider="anthropic",
            input_tokens=5,
            output_tokens=5,
            cache_read_tokens=2,
            cache_write_tokens=0,
            tool_counts={"Read": 2},
        ),
        subagent_row(
            "child-d",
            parent=by_key["other-provider"],
            role=_AUDITOR,
            skill="planner",
            provider="minimax",
            input_tokens=500,
            output_tokens=25,
            cache_read_tokens=20,
            cache_write_tokens=5,
            tool_counts={"Bash": 3},
        ),
        subagent_row(
            "child-e",
            parent=by_key["codex"],
            role="codex-reviewer",
            skill="planner",
            provider="codex",
            input_tokens=9,
            output_tokens=3,
            cache_read_tokens=0,
            cache_write_tokens=0,
            tool_counts={"Read": 1},
        ),
        subagent_row(
            "child-f",
            parent=by_key["review"],
            role="unresolved-observer",
            skill="review",
            provider="anthropic",
            input_tokens=None,
            output_tokens=None,
            cache_read_tokens=None,
            cache_write_tokens=None,
            tool_counts={},
            transcript_state="unknown",
            usage_state="unknown",
        ),
        subagent_row(
            "child-g",
            parent=by_key["review"],
            role="zero-denominator",
            skill="review",
            provider="anthropic",
            input_tokens=2,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            tool_counts={"Read": 1},
        ),
    ]
    children[4]["token_usage"]["cache_read_tokens"] = token_measure(None, state="unavailable")
    request_rows = [
        {"request_id": "unlinked-request", "agent_name": _AUDITOR, "input_tokens": 999_999}
    ]
    tool_rows = [{"agent_name": _AUDITOR, "tool_name": "Bash", "count": 999_999}]
    return sessions, request_rows, tool_rows, children


def _source_records(
    rows: list[dict[str, Any]],
    *,
    child: bool = False,
    parents: dict[str, dict[str, Any]] | None = None,
) -> list[MeasureRecord]:
    if child and parents is None:
        raise ValueError("child records require their parent rows")
    parent_rows = parents or {}
    records = []
    for row in rows:
        raw = row["token_usage"] if child else row
        harness = parent_rows[row["parent_session_key"]]["harness"] if child else row["harness"]
        provider = row["provider"]
        records.append(
            MeasureRecord(
                SourcePair(harness, provider),
                {field: TokenMeasure.from_dict(raw[field]) for field in _TOKEN_FIELDS},
            )
        )
    return records


def _definition(name: str, body: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description="Canonical reviewer role.",
        body=body,
        tools=("Bash", "Read"),
        model="test-claude-model",
        reader_tools=(),
        codex=SimpleNamespace(model="test-codex-model"),
    )


def _metric_row(
    blocks: list[dict[str, Any]], *, window: str, levels: set[str | None]
) -> dict[str, Any]:
    block = next(
        block for block in blocks if block["window"] == window and set(block["levels"]) == levels
    )
    return block


def _assert_serialized_measure(actual: dict[str, Any], expected: Any) -> None:
    assert actual["state"] == expected.value.state.value
    assert actual["value"] == expected.value.value
    assert actual["state_counts"] == {
        state.value: count for state, count in expected.state_counts.items()
    }


def _assert_serialized_ratio(actual: dict[str, Any], expected: Any, *, sample_unit: str) -> None:
    assert actual["state"] == expected.state.value
    assert actual["value"] == expected.value
    assert actual["numerator"] == expected.numerator_field
    assert actual["denominator"] == expected.denominator_field
    assert actual["numerator_total"] == expected.numerator_total
    assert actual["denominator_total"] == expected.denominator_total
    assert actual["sample_unit"] == sample_unit
    assert actual["sample_size"] == expected.sample_size
    assert actual["excluded_runs"] == expected.excluded_runs
    assert actual["unknown_runs"] == expected.unknown_runs


def _tool_records(
    rows: list[dict[str, Any]], parents: dict[str, dict[str, Any]]
) -> list[MeasureRecord]:
    tools = sorted({tool for row in rows for tool in row["tool_counts"]})
    base_records = _source_records(rows, child=True, parents=parents)
    records = []
    for row, base in zip(rows, base_records, strict=True):
        measures = dict(base.measures)
        observed = row["transcript_state"] == "observed"
        measures["tool_calls"] = (
            TokenMeasure.observed(sum(row["tool_counts"].values()))
            if observed
            else TokenMeasure.unknown()
        )
        for tool in tools:
            measures[f"tool:{tool}"] = (
                TokenMeasure.observed(row["tool_counts"].get(tool, 0))
                if observed
                else TokenMeasure.unknown()
            )
        records.append(MeasureRecord(base.pair, measures))
    return records


def test_encode_table_aligns_rows_to_columns_and_fills_missing_keys() -> None:
    encoded = encode_table([{"key": "row-1", "time_ms": 42}], SESSION_COLUMNS)

    assert encoded["columns"] == list(SESSION_COLUMNS)
    assert encoded["rows"] == [[{"key": "row-1", "time_ms": 42}.get(c) for c in SESSION_COLUMNS]]
    assert encoded["rows"][0][SESSION_COLUMNS.index("time_ms")] == 42
    assert encoded["rows"][0][SESSION_COLUMNS.index("harness")] is None


def test_payload_orders_rows_by_timestamp_then_key_and_handles_epoch() -> None:
    rows = [
        _row("late", time_ms=2),
        _row("untimed"),
        _row("same-b", time_ms=1),
        _row("epoch", time_ms=0),
        _row("same-a", time_ms=1),
    ]
    payload = _payload(rows)
    table = payload["tables"][SESSION_TABLE]
    keys = [row[table["columns"].index("key")] for row in table["rows"]]

    assert keys == ["epoch", "same-a", "same-b", "late", "untimed"]
    assert payload["generated_at_ms"] == GEN_MS
    assert payload["index_schema_version"] == 7
    assert payload["landing"] == LANDING_VIEW


def test_payload_rejects_naive_generated_at() -> None:
    with pytest.raises(ValueError):
        build_deck_payload([], generated_at=datetime(2026, 10, 4), index_schema_version=1)


def test_observed_harness_and_provider_chips_are_live_and_sorted_by_count() -> None:
    rows = [
        _row("cc-1", provider="anthropic"),
        _row("cc-2", provider="anthropic"),
        _row("cc-3", provider="anthropic"),
        _row("codex-1", harness="codex", provider="unknown"),
    ]
    chips = _payload(rows)["chips"]["cohort"]
    harness = chips["harness"]
    provider = chips["provider"]

    assert [(chip["key"], chip["count"], chip["state"]) for chip in harness] == [
        ("claude-code", 3, "live"),
        ("codex", 1, "live"),
    ]
    assert [(chip["key"], chip["count"]) for chip in provider] == [
        ("anthropic", 3),
        ("unknown", 1),
    ]
    assert next(chip for chip in provider if chip["key"] == "unknown")["state"] == "live"
    assert all(chip["key"] == chip["match"] for chip in harness + provider)
    assert all(chip["key"] == chip["label"] for chip in harness + provider)


def test_cohort_l0_chip_is_struck_with_its_registry_reason() -> None:
    rows = [_row("one", level="skill")]
    chips = _payload(rows)["chips"]["cohort"]
    l0 = _chip(chips, "level", "L0")

    assert l0["state"] == "struck"
    assert (
        l0["reason"]
        == "L0 leaf agents write no session row; they exist only as subagent transcripts"
    )
    assert l0["issue"] is None


def test_unrecorded_level_values_use_gap_reason_and_keep_null_bucket() -> None:
    rows = [_row("one"), _row("two")]
    chips = _payload(rows)["chips"]["cohort"]
    levels = chips["level"]

    for key in ("L1", "L2", "L3"):
        chip = _chip(chips, "level", key)
        assert chip["state"] == "absent"
        assert chip["issue"] == 4622
        assert chip["reason"] == "no session row in this index records its orchestration level"

    l0 = _chip(chips, "level", "L0")
    unrecorded = _chip(chips, "level", "unrecorded")
    assert l0["match"] is None
    assert unrecorded["key"] == unrecorded["label"] == "unrecorded"
    assert unrecorded["match"] is None
    assert unrecorded["state"] == "live"
    assert unrecorded["count"] == len(rows)
    assert len(levels) == 5


def test_absent_fleet_chip_without_level_gap_has_no_issue() -> None:
    chips = _payload([_row("skill", level="skill"), _row("orchestrator", level="orchestrator")])[
        "chips"
    ]["cohort"]
    l3 = _chip(chips, "level", "L3")

    assert l3["state"] == "absent"
    assert l3["reason"] == "no L3 rows in this index"
    assert l3["issue"] is None


def test_windows_report_recent_data_and_limited_retention() -> None:
    rows = [
        _row("one-day", time_ms=GEN_MS - 86_400_000),
        _row("nine-days", time_ms=GEN_MS - 9 * 86_400_000),
    ]
    windows = _payload(rows)["chips"]["cohort"]["window"]
    seven = _chip({"window": windows}, "window", "7d")
    twenty_eight = _chip({"window": windows}, "window", "28d")
    all_history = _chip({"window": windows}, "window", "all")

    assert (seven["state"], seven["count"]) == ("live", 1)
    assert twenty_eight["state"] == "absent"
    assert twenty_eight["issue"] == 4621
    assert twenty_eight["reason"] == "index history begins 2026-09-25 — 9 days retained"
    assert (all_history["state"], all_history["count"]) == ("live", 2)


def test_windows_without_recent_rows_and_untimed_history() -> None:
    older_rows = [
        _row("forty-days", time_ms=GEN_MS - 40 * 86_400_000),
        _row("sixty-days", time_ms=GEN_MS - 60 * 86_400_000),
    ]
    older = _payload(older_rows)["chips"]["cohort"]["window"]
    seven = _chip({"window": older}, "window", "7d")
    twenty_eight = _chip({"window": older}, "window", "28d")
    all_history = _chip({"window": older}, "window", "all")
    assert (seven["state"], seven["reason"], seven["issue"]) == (
        "absent",
        "no rows in the last 7 days",
        None,
    )
    assert (twenty_eight["state"], twenty_eight["reason"], twenty_eight["issue"]) == (
        "absent",
        "no rows in the last 28 days",
        None,
    )
    assert (all_history["state"], all_history["count"]) == ("live", 2)

    untimed = _payload([_row("untimed-a"), _row("untimed-b")])["chips"]["cohort"]["window"]
    assert all(chip["state"] == "absent" and chip["issue"] is None for chip in untimed[:-1])
    assert untimed[-1]["key"] == "all"
    assert (untimed[-1]["state"], untimed[-1]["count"]) == ("live", 2)


def test_payload_shape_history_availability_and_json_safety() -> None:
    rows = [
        _row("first", time_ms=1),
        _row("last", time_ms=9),
        _row("untimed"),
    ]
    payload = _payload(rows)
    built_ids = [view.view_id for view in DECK_VIEWS if view.planned_issue is None]

    assert payload["views"] == [
        {
            "id": view.view_id,
            "question": view.question,
            "decision": view.decision,
            "group": view.group,
            "status": "built" if view.planned_issue is None else "planned",
            "issue": view.planned_issue,
        }
        for view in DECK_VIEWS
    ]
    assert set(payload["chips"]) == set(built_ids)
    assert payload["facets"][-1] == {
        "id": "window",
        "label": "window",
        "column": "time_ms",
        "kind": "window",
    }
    assert payload["availability"] == [
        {
            "state": state.value,
            "label": AVAILABILITY_VOCABULARY[state][0],
            "description": AVAILABILITY_VOCABULARY[state][1],
        }
        for state in TokenMeasureState
    ]
    assert payload["history"] == {"first_ms": 1, "last_ms": 9, "untimed": 1}
    json.dumps(payload, allow_nan=False)


def test_empty_index_has_absent_declared_values_and_empty_observed_facets() -> None:
    payload = _payload([])
    chips = payload["chips"]["cohort"]

    assert payload["tables"][SESSION_TABLE]["rows"] == []
    assert payload["history"] == {"first_ms": None, "last_ms": None, "untimed": 0}
    assert chips["harness"] == []
    assert chips["provider"] == []
    for key in ("L1", "L2", "L3"):
        chip = _chip(chips, "level", key)
        assert (chip["state"], chip["issue"]) == ("absent", 4622)
    assert all(chip["state"] == "absent" for chip in chips["window"][:-1])
    assert (chips["window"][-1]["key"], chips["window"][-1]["state"]) == ("all", "live")


def test_prepared_metrics_keep_source_pairs_roles_and_shared_library_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, requests, tools, children = _full_index_facts()
    body = "Bash runs git show to inspect the committed slice."
    loader_calls: list[None] = []

    def definition_loader() -> tuple[SimpleNamespace, ...]:
        loader_calls.append(None)
        return (_definition(_AUDITOR, body),)

    monkeypatch.setattr(
        deck_payload_module,
        "load_bundled_agent_definitions",
        definition_loader,
        raising=False,
    )
    aggregate_calls: list[int] = []
    ratio_calls: list[int] = []
    original_aggregate = getattr(deck_payload_module, "aggregate_measures", aggregate_measures)
    original_ratio = getattr(deck_payload_module, "measure_ratio", measure_ratio)

    def aggregate_spy(records: Any, fields: Any, **kwargs: Any) -> Any:
        rows = list(records)
        aggregate_calls.append(len(rows))
        return original_aggregate(rows, fields, **kwargs)

    def ratio_spy(records: Any, numerator: str, denominator: str, **kwargs: Any) -> Any:
        rows = list(records)
        ratio_calls.append(len(rows))
        return original_ratio(rows, numerator, denominator, **kwargs)

    monkeypatch.setattr(deck_payload_module, "aggregate_measures", aggregate_spy, raising=False)
    monkeypatch.setattr(deck_payload_module, "measure_ratio", ratio_spy, raising=False)
    payload = build_deck_payload(
        sessions,
        request_rows=requests,
        tool_rows=tools,
        subagent_rows=children,
        generated_at=DECK_GENERATED_AT,
        index_schema_version=7,
    )

    assert aggregate_calls and ratio_calls
    assert len(loader_calls) == 1
    prepared = payload["prepared"]
    built_views = {view.view_id for view in DECK_VIEWS if view.planned_issue is None}
    assert set(prepared["view_chips"]) == built_views
    assert set(prepared["view_histories"]) == built_views
    assert set(payload["tables"]) == {SESSION_TABLE, "skills", "roles"}
    levels: set[str | None] = {"orchestrator", "skill"}
    skill_block = _metric_row(prepared["skills"], window="all", levels=levels)
    planner = next(
        row
        for row in skill_block["rows"]
        if (row["skill"], row["harness"], row["provider"])
        == ("planner", "claude-code", "anthropic")
    )
    planner_runs = [
        row
        for row in sessions
        if row["skill"] == "planner"
        and row["harness"] == "claude-code"
        and row["provider"] == "anthropic"
        and row["level"] in levels
    ]
    planner_aggregate = aggregate_measures(_source_records(planner_runs), _TOKEN_FIELDS)
    planner_ratio = measure_ratio(_source_records(planner_runs), "input_tokens", "output_tokens")
    assert planner["measures"]["input_tokens"]["value"] == 30
    assert planner["exact_retransmission"] == {"state": "unavailable", "value": None}
    assert planner["recipe_steps"][0]["exact_retransmission"] == {
        "state": "unavailable",
        "value": None,
    }
    _assert_serialized_measure(
        planner["measures"]["input_tokens"], planner_aggregate.fields["input_tokens"]
    )
    _assert_serialized_ratio(
        planner["ratios"]["input_output"], planner_ratio, sample_unit="skill-run"
    )

    role_block = _metric_row(prepared["roles"], window="all", levels=levels)
    auditor = next(
        row
        for row in role_block["rows"]
        if (row["role"], row["provider"]) == (_AUDITOR, "anthropic")
    )
    claude_metrics = next(row for row in auditor["harnesses"] if row["harness"] == "claude-code")
    auditor_runs = [
        row
        for row in children
        if row["role"] == _AUDITOR and row["provider"] == "anthropic" and row["level"] in levels
    ]
    parents = {row["key"]: row for row in sessions}
    child_records = _source_records(auditor_runs, child=True, parents=parents)
    child_aggregate = aggregate_measures(child_records, _TOKEN_FIELDS)
    child_ratio = measure_ratio(child_records, "input_tokens", "output_tokens")
    tool_records = _tool_records(auditor_runs, parents)
    bash_ratio = measure_ratio(tool_records, "tool:Bash", "tool_calls")
    assert claude_metrics["measures"]["input_tokens"]["value"] == 105
    _assert_serialized_measure(
        claude_metrics["measures"]["input_tokens"], child_aggregate.fields["input_tokens"]
    )
    _assert_serialized_ratio(
        claude_metrics["ratios"]["input_output"], child_ratio, sample_unit="child-invocation"
    )
    _assert_serialized_ratio(
        claude_metrics["ratios"]["tool_mix"]["Bash"],
        bash_ratio,
        sample_unit="child-invocation",
    )
    assert claude_metrics["ratios"]["input_output"]["sample_size"] == 3
    assert claude_metrics["ratios"]["tool_mix"]["Bash"]["review_eligible"] is True
    assert claude_metrics["ratios"]["tool_mix"]["Bash"]["definition_roles"] == [_AUDITOR]

    zero_skill_block = _metric_row(prepared["skills"], window="all", levels={"skill"})
    maintenance = next(row for row in zero_skill_block["rows"] if row["skill"] == "maintenance")
    zero_ratio = maintenance["ratios"]["input_output"]
    assert (zero_ratio["state"], zero_ratio["value"], zero_ratio["sample_size"]) == (
        "measured_zero",
        0.0,
        1,
    )

    unresolved = next(row for row in role_block["rows"] if row["role"] == "unresolved-observer")
    unresolved_ratio = unresolved["harnesses"][0]["ratios"]["input_output"]
    assert unresolved_ratio["state"] == "unknown"
    assert unresolved_ratio["review_eligible"] is False
    assert unresolved_ratio["review_reason"]
    assert prepared["definitions"][_AUDITOR]["state"] == "available"
    assert prepared["definitions"][_AUDITOR]["body"] == body
    assert prepared["definitions"]["unresolved-observer"]["state"] == "unavailable"

    zero_denominator = next(row for row in role_block["rows"] if row["role"] == "zero-denominator")
    zero_denominator_ratio = zero_denominator["harnesses"][0]["ratios"]["input_output"]
    assert (zero_denominator_ratio["state"], zero_denominator_ratio["sample_size"]) == (
        "not_applicable",
        1,
    )
    codex = next(row for row in role_block["rows"] if row["role"] == "codex-reviewer")
    codex_cache = codex["harnesses"][0]["ratios"]["cache_share"]
    assert (codex_cache["state"], codex_cache["excluded_runs"]) == ("unavailable", 1)

    def table_rows(table: dict[str, Any]) -> list[dict[str, Any]]:
        return [dict(zip(table["columns"], row, strict=True)) for row in table["rows"]]

    skill_identities = table_rows(payload["tables"]["skills"])
    role_identities = table_rows(payload["tables"]["roles"])
    assert {(row["skill"], row["harness"], row["provider"]) for row in skill_identities} >= {
        ("planner", "claude-code", "anthropic"),
        ("planner", "claude-code", "minimax"),
        ("planner", "codex", "codex"),
    }
    assert {(row["role"], row["provider"]) for row in role_identities} >= {
        (_AUDITOR, "anthropic"),
        (_AUDITOR, "minimax"),
    }
    assert {
        (edge["skill"], edge["role"], edge["harness"], edge["provider"])
        for edge in prepared["relationships"]
    } >= {
        ("planner", _AUDITOR, "claude-code", "anthropic"),
        ("review", _AUDITOR, "claude-code", "anthropic"),
        ("planner", _AUDITOR, "claude-code", "minimax"),
    }
    assert prepared["view_chips"] == payload["chips"]
    assert prepared["view_histories"]["skill"]["last_ms"] == sessions[-1]["time_ms"]
    assert (
        prepared["view_histories"]["role"]["last_ms"]
        < prepared["view_histories"]["skill"]["last_ms"]
    )
    json.dumps(payload, allow_nan=False)


def test_observed_zero_ratio_can_be_flagged_with_complete_definitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, _, _, children = _full_index_facts()
    for child in children:
        if child.get("role") == _AUDITOR:
            child["token_usage"]["cache_read_tokens"] = token_measure(0)
    monkeypatch.setattr(
        deck_payload_module,
        "load_bundled_agent_definitions",
        lambda: (_definition(_AUDITOR, "Inspect the slice with git show."),),
    )
    payload = build_deck_payload(
        sessions,
        subagent_rows=children,
        generated_at=DECK_GENERATED_AT,
        index_schema_version=7,
    )
    block = _metric_row(payload["prepared"]["roles"], window="all", levels={"skill"})
    auditor = next(
        row for row in block["rows"] if row["role"] == _AUDITOR and row["provider"] == "anthropic"
    )
    signal = auditor["harnesses"][0]["ratios"]["cache_share"]
    assert (signal["state"], signal["value"]) == ("measured_zero", 0.0)
    assert signal["sample_size"] > 0
    assert signal["definition_roles"] == [_AUDITOR]
    assert signal["review_eligible"] is True
    assert signal["review_reason"] is None
