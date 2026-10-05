"""Tests for encoding the report deck's index data contract."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from autoskillit.core import TokenMeasureState
from autoskillit.report.deck import build_deck_payload
from autoskillit.report.deck._payload import encode_table
from autoskillit.report.deck._registry import (
    AVAILABILITY_VOCABULARY,
    DECK_VIEWS,
    LANDING_VIEW,
    SESSION_COLUMNS,
    SESSION_TABLE,
)
from tests.report._fixtures import DECK_GENERATED_AT
from tests.report._fixtures import session_row as _row

pytestmark = [pytest.mark.small]
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
GEN_MS = (DECK_GENERATED_AT - EPOCH) // timedelta(milliseconds=1)


def _payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return build_deck_payload(rows, generated_at=DECK_GENERATED_AT, index_schema_version=7)


def _chip(chips: dict[str, list[dict[str, Any]]], facet: str, key: str) -> dict[str, Any]:
    return next(chip for chip in chips[facet] if chip["key"] == key)


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
