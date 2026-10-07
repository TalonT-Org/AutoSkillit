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
from autoskillit.report.deck import _measure_helpers as deck_measure_helpers
from autoskillit.report.deck import _payload as deck_payload_module
from autoskillit.report.deck import build_deck_payload
from autoskillit.report.deck._payload import encode_table
from autoskillit.report.deck._registry import (
    AVAILABILITY_VOCABULARY,
    DECK_VIEWS,
    ERROR_TABLE,
    GAP_TABLE,
    LANDING_VIEW,
    PARITY_TABLE,
    SESSION_COLUMNS,
    SESSION_TABLE,
    TREND_TABLE,
    TURN_TABLE,
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


@pytest.fixture
def prepared_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
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
    original_aggregate = deck_measure_helpers.aggregate_measures
    original_ratio = deck_measure_helpers.measure_ratio

    def aggregate_spy(records: Any, fields: Any, **kwargs: Any) -> Any:
        rows = list(records)
        aggregate_calls.append(len(rows))
        return original_aggregate(rows, fields, **kwargs)

    def ratio_spy(records: Any, numerator: str, denominator: str, **kwargs: Any) -> Any:
        rows = list(records)
        ratio_calls.append(len(rows))
        return original_ratio(rows, numerator, denominator, **kwargs)

    monkeypatch.setattr(deck_measure_helpers, "aggregate_measures", aggregate_spy)
    monkeypatch.setattr(deck_measure_helpers, "measure_ratio", ratio_spy)
    payload = build_deck_payload(
        sessions,
        request_rows=requests,
        tool_rows=tools,
        subagent_rows=children,
        generated_at=DECK_GENERATED_AT,
        index_schema_version=7,
    )

    return SimpleNamespace(
        payload=payload,
        sessions=sessions,
        children=children,
        definition_body=body,
        aggregate_calls=aggregate_calls,
        ratio_calls=ratio_calls,
        loader_calls=loader_calls,
    )


def test_prepared_skill_and_child_metrics_use_shared_accounting(
    prepared_metrics: SimpleNamespace,
) -> None:
    payload = prepared_metrics.payload
    sessions = prepared_metrics.sessions
    children = prepared_metrics.children
    assert prepared_metrics.aggregate_calls and prepared_metrics.ratio_calls
    assert len(prepared_metrics.loader_calls) == 1
    prepared = payload["prepared"]
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


def test_prepared_ratio_states_and_definition_availability(
    prepared_metrics: SimpleNamespace,
) -> None:
    prepared = prepared_metrics.payload["prepared"]
    role_block = _metric_row(prepared["roles"], window="all", levels={"orchestrator", "skill"})
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
    assert prepared["definitions"][_AUDITOR]["body"] == prepared_metrics.definition_body
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


def test_prepared_identity_tables_and_view_projections(prepared_metrics: SimpleNamespace) -> None:
    payload = prepared_metrics.payload
    sessions = prepared_metrics.sessions
    prepared = payload["prepared"]
    built_views = {view.view_id for view in DECK_VIEWS if view.planned_issue is None}
    assert set(prepared["view_chips"]) == built_views
    assert set(prepared["view_histories"]) == built_views
    assert set(payload["tables"]) == {
        SESSION_TABLE,
        "skills",
        "roles",
        TURN_TABLE,
        ERROR_TABLE,
        TREND_TABLE,
        GAP_TABLE,
        PARITY_TABLE,
    }

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


def _view_block(payload: dict[str, Any], view: str, window: str = "all") -> dict[str, Any]:
    return next(
        block
        for block in payload["prepared"][view]["blocks"]
        if block["window"] == window and block["levels"] == ["skill"]
    )


def _turn_fact(
    session: dict[str, Any],
    *,
    time_ms: int,
    model: str = "claude-opus-4-1-20250805",
) -> dict[str, Any]:
    return {
        "key": f"{session['key']}:turn:0",
        "kind": "turn",
        "session_key": session["key"],
        "source_id": session["key"],
        "session_id": session["session_id"],
        "ordinal": 0,
        "request_id": "req-1",
        "message_id": "message-1",
        "time_ms": time_ms,
        "harness": session["harness"],
        "provider": session["provider"],
        "model": model,
        "skill": session["skill"],
        "recipe": session["recipe"],
        "step": session["step"],
        "level": session["level"],
        "input_tokens": token_measure(75),
        "output_tokens": token_measure(0),
        "cache_read_tokens": token_measure(50),
        "cache_write_tokens": token_measure(None, state="unavailable"),
        "context_window_tokens": 100,
        "context_fraction": 0.5,
    }


def test_context_rows_keep_exact_turn_owner_model_states_and_ratio() -> None:
    owner = _row(
        "owner",
        session_id="native-owner",
        time_ms=GEN_MS - 1_000,
        level="skill",
        skill="demo",
        recipe="recipe-demo",
        step="run",
        turn_usage_state="observed",
        turn_usage_reason=None,
    )
    turn = _turn_fact(owner, time_ms=GEN_MS - 900)
    payload = build_deck_payload(
        [owner],
        request_rows=[
            {
                "key": "native-owner:req-1",
                "session_key": "owner",
                "request_id": "req-1",
                "model": "claude-opus-4-1-20250805",
            }
        ],
        turn_rows=(row for row in [turn]),
        generated_at=DECK_GENERATED_AT,
        index_schema_version=7,
    )

    turns_table = payload["tables"][TURN_TABLE]
    turn_row = dict(zip(turns_table["columns"], turns_table["rows"][0], strict=True))
    assert turn_row["session_key"] == "owner"
    assert turn_row["model"] == "claude-opus-4-1-20250805"
    assert turn_row["output_tokens"] == token_measure(0)
    assert turn_row["cache_write_tokens"] == token_measure(None, state="unavailable")

    context = _view_block(payload, "context")
    prepared_turn = next(row for row in context["rows"] if row["kind"] == "turn")
    assert prepared_turn["session_key"] == "owner"
    assert prepared_turn["context_fraction_percent"] == {
        "state": "measured",
        "value": 50.0,
        "sample_size": 1,
        "excluded_runs": 0,
        "unknown_runs": 0,
    }
    assert prepared_turn["fraction_disagreement"] is False
    assert prepared_turn["request_recorded"] is True
    assert context["sessions"]["owner"]["rows"] == [prepared_turn]


def test_context_keeps_ordinal_order_across_an_untimed_missing_window_turn() -> None:
    owner = _row(
        "ordered-owner",
        time_ms=GEN_MS - 1_000,
        level="skill",
        skill="demo",
        recipe="recipe-demo",
        step="run",
        turn_usage_state="observed",
    )
    turns = [
        _turn_fact(owner, time_ms=GEN_MS - 900),
        _turn_fact(owner, time_ms=None),
        _turn_fact(owner, time_ms=GEN_MS - 700),
    ]
    for ordinal, turn in enumerate(turns):
        turn["ordinal"] = ordinal
        turn["key"] = f"ordered-owner:turn:{ordinal}"
        turn["context_window_tokens"] = None if ordinal == 1 else 100
        turn["context_fraction"] = None if ordinal == 1 else 0.5
    payload = build_deck_payload(
        [owner],
        turn_rows=turns,
        generated_at=DECK_GENERATED_AT,
        index_schema_version=7,
    )

    rows = _view_block(payload, "context")["rows"]
    assert [row["ordinal"] for row in rows] == [0, 1, 2]
    assert rows[1]["time_ms"] is None
    assert rows[1]["context_fraction_percent"]["state"] == "unknown"
    assert rows[1]["context_fraction_percent"]["value"] is None


def test_parent_span_provenance_is_json_safe_after_measure_aggregation() -> None:
    owner = _row(
        "span-owner",
        session_id="native-span-owner",
        time_ms=GEN_MS - 1_000,
        level="skill",
        skill="demo",
        recipe="recipe-demo",
        step="run",
    )
    spans = [
        {
            "field": "parent_prompt_tokens",
            "measure": TokenMeasure.observed(6).to_dict(),
            "invocation_id": "call-1",
            "turn_id": "turn-1",
            "source_id": "parent-prompt",
            "timestamp": DECK_GENERATED_AT.isoformat(),
            "harness": "claude-code",
            "provider": "anthropic",
            "model": "model-x",
            "tokenizer_version": "test",
            "encoding": "test",
            "reason": None,
        },
        {
            "field": "subagent_return_tokens",
            "measure": TokenMeasure.observed(4).to_dict(),
            "invocation_id": "call-1",
            "turn_id": "turn-1",
            "source_id": "parent-return",
            "timestamp": DECK_GENERATED_AT.isoformat(),
            "harness": "claude-code",
            "provider": "anthropic",
            "model": "model-x",
            "tokenizer_version": "test",
            "encoding": "test",
            "reason": None,
        },
    ]
    child = {
        "key": "span-owner:child-1",
        "child_id": "child-1",
        "role": _AUDITOR,
        "actor_level": "L0",
        "parent_session_key": owner["key"],
        "native_parent_session_id": owner["session_id"],
        "provider": "anthropic",
        "parent_context_spans": spans,
    }
    payload = build_deck_payload(
        [owner],
        subagent_rows=[child],
        generated_at=DECK_GENERATED_AT,
        index_schema_version=7,
    )

    encoded = json.dumps(payload, allow_nan=False)
    context = _view_block(payload, "context")
    model_row = next(
        row for row in context["metrics"]["parent_context"] if row["model"] == "model-x"
    )
    assert len(encoded) > 0
    assert model_row["provenance"][0]["measure"] == {"state": "measured", "value": 6}
    assert model_row["provenance"][1]["measure"] == {"state": "measured", "value": 4}


def test_errors_keep_session_and_tool_failures_separate_with_known_denominators() -> None:
    common = {
        "time_ms": GEN_MS - 1_000,
        "level": "skill",
        "skill": "demo",
        "recipe": "recipe-demo",
        "step": "run",
    }
    sessions = [
        _row("failed", **common, success=False, adjudication_reason="timeout"),
        _row("passed", **common, success=True),
        _row("unknown", **common, success=None),
    ]
    tools = [
        {
            "key": "failed:tool-1",
            "session_key": "failed",
            "time_ms": GEN_MS - 900,
            "success": False,
            "error_type": "ToolError",
        },
        {
            "key": "unknown:tool-2",
            "session_key": "unknown",
            "time_ms": GEN_MS - 800,
            "success": None,
            "error_type": "ToolOutcomeUnknown",
        },
        {
            "key": "orphan:tool-3",
            "session_key": None,
            "time_ms": GEN_MS - 700,
            "success": False,
            "error_type": "OrphanError",
        },
    ]
    payload = build_deck_payload(
        sessions,
        tool_rows=tools,
        generated_at=DECK_GENERATED_AT,
        index_schema_version=7,
    )
    rows = _view_block(payload, "errors")["rows"]
    session_error = next(row for row in rows if row["population"] == "session_failure")
    assert session_error["symptom"] == "timeout"
    assert session_error["failures"]["value"] == 1
    assert session_error["failure_rate"]["value"] == 0.5
    assert session_error["eligible_count"] == 3
    assert session_error["observed_count"] == 2
    assert session_error["unknown_count"] == 1

    tool_error = next(row for row in rows if row["symptom"] == "ToolError")
    unknown_tool = next(row for row in rows if row["symptom"] == "ToolOutcomeUnknown")
    orphan = next(row for row in rows if row["population"] == "unattributed_tool")
    assert tool_error["population"] == "tool_failure"
    assert tool_error["failures"]["value"] == 1
    assert unknown_tool["failures"]["state"] == "unknown"
    assert orphan["harness"] is None and orphan["provider"] is None
    assert orphan["attribution_state"] == "unattributed"


def test_trends_use_utc_days_and_disclose_future_untimed_and_interval_samples() -> None:
    day_ms = 86_400_000
    sessions = [
        _row(
            "earlier",
            time_ms=GEN_MS - 5 * day_ms,
            level="skill",
            skill="demo",
            recipe="recipe-demo",
            step="run",
            model="model-a",
            success=True,
            input_tokens=token_measure(10),
        ),
        _row(
            "later",
            time_ms=GEN_MS - day_ms,
            level="skill",
            skill="demo",
            recipe="recipe-demo",
            step="run",
            model="model-a",
            success=False,
            input_tokens=token_measure(30),
        ),
        _row(
            "future",
            time_ms=GEN_MS + 1,
            level="skill",
            skill="demo",
            recipe="recipe-demo",
            step="run",
            model="model-a",
            success=False,
            input_tokens=token_measure(999),
        ),
        _row(
            "untimed",
            level="skill",
            skill="demo",
            recipe="recipe-demo",
            step="run",
            model="model-a",
            success=None,
            input_tokens=token_measure(100),
        ),
    ]
    payload = build_deck_payload(
        sessions,
        generated_at=DECK_GENERATED_AT,
        index_schema_version=7,
    )
    trend = _view_block(payload, "trend", "7d")
    assert trend["metrics"]["future_sessions"] == 1
    assert trend["metrics"]["untimed_sessions"] == 1
    assert all(row["time_ms"] <= GEN_MS for row in trend["rows"])
    comparison = trend["metrics"]["comparisons"][0]
    assert comparison["earlier"]["measures"]["input_tokens"]["value"] == 10.0
    assert comparison["later"]["measures"]["input_tokens"]["value"] == 30.0
    assert comparison["deltas"]["input_tokens"]["value"] == 20.0
    assert comparison["deltas"]["failure_share"]["value"] == 100.0
    assert any(
        row["eligible_sessions"] == 0
        and row["measures"]["input_tokens"]["state"] == "no_observations"
        for row in trend["rows"]
    )


def test_gap_and_parity_share_missing_evidence_states_and_session_selection() -> None:
    owner = _row(
        "empty-ledger",
        time_ms=GEN_MS - 1_000,
        level="skill",
        skill="demo",
        recipe="recipe-demo",
        step="run",
        turn_usage_state="unavailable",
        turn_usage_reason="turn_usage_ledger_missing",
    )
    payload = build_deck_payload(
        [owner],
        generated_at=DECK_GENERATED_AT,
        index_schema_version=7,
    )
    gaps = _view_block(payload, "gaps")
    parity = _view_block(payload, "parity")
    parity_by_key = {row["key"]: row for row in parity["rows"]}
    assert all(parity_by_key[row["key"]]["state"] == row["state"] for row in gaps["rows"])
    turn_series = next(
        row
        for row in parity["rows"]
        if row["field"] == "turn_series_coverage" and row["population"] == "turn"
    )
    missing_tools = next(
        row
        for row in parity["rows"]
        if row["field"] == "tool_errors" and row["population"] == "tool_result"
    )
    prompt = next(
        row
        for row in parity["rows"]
        if row["field"] == "parent_prompt_tokens" and row["population"] == "parent_context"
    )
    assert turn_series["state"] == "unavailable"
    assert "turn_usage_ledger_missing" in turn_series["reason"]
    assert missing_tools["state"] == "no_observations"
    assert prompt["state"] == "no_observations"
    assert gaps["sessions"]["empty-ledger"]["rows"]
