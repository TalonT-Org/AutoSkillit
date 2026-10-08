from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from autoskillit.core import (
    MeasureRecord,
    TokenMeasure,
    TokenMeasureState,
)

from ._measure_helpers import (
    TOKEN_FIELDS,
    _aggregate_fields,
    _coverage_state,
    _measure_records,
    _outcome_summary,
    _ratio_field,
    _source_pair,
    _state_counts_empty,
)
from ._registry import WINDOWS
from ._view_common import DAY_MS


def _trend_measure_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "measures": {
                field: {
                    "state": "no_observations",
                    "value": None,
                    "observed_sessions": 0,
                    "eligible_sessions": 0,
                    "state_counts": _state_counts_empty(),
                }
                for field in TOKEN_FIELDS
            },
            "failure_share": {
                "state": "no_observations",
                "value": None,
                "sample_size": 0,
                "excluded_runs": 0,
                "unknown_runs": 0,
                "eligible_count": 0,
                "observation_count": 0,
                "state_counts": _state_counts_empty(),
            },
        }
    base_records = _measure_records(rows)
    coverage = _aggregate_fields(base_records, TOKEN_FIELDS, eligible_count=len(rows))
    measures: dict[str, Any] = {}
    for field in TOKEN_FIELDS:
        known = [
            record
            for record in base_records
            if record.measures[field].state
            in (TokenMeasureState.MEASURED, TokenMeasureState.MEASURED_ZERO)
        ]
        ratio_records = [
            MeasureRecord(
                record.pair,
                {field: record.measures[field], "observed_sessions": TokenMeasure.observed(1)},
            )
            for record in known
        ]
        average = _ratio_field(ratio_records, field, "observed_sessions", eligible_count=len(rows))
        state_counts = coverage[field]["state_counts"]
        measures[field] = {
            "state": (
                average["state"]
                if average["value"] is not None
                else _coverage_state(state_counts, len(rows))
            ),
            "value": average["value"],
            "observed_sessions": average["sample_size"],
            "eligible_sessions": len(rows),
            "state_counts": state_counts,
        }
    outcome = _outcome_summary(rows)
    failure_share = {
        **outcome["failure_rate"],
        "state_counts": outcome.get("state_counts", _state_counts_empty()),
        "eligible_sessions": len(rows),
        "observed_sessions": outcome["observed_count"],
        "unknown_sessions": outcome["unknown_count"],
    }
    return {"measures": measures, "failure_share": failure_share}


def _trend_group_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    pair = _source_pair(row)
    return (
        row.get("skill") if isinstance(row.get("skill"), str) else None,
        row.get("recipe") if isinstance(row.get("recipe"), str) else None,
        row.get("step") if isinstance(row.get("step"), str) else None,
        pair.harness,
        pair.provider,
        row.get("model") if isinstance(row.get("model"), str) else None,
    )


def _interval_summary(
    rows: Sequence[Mapping[str, Any]], *, start_ms: int, end_ms: int
) -> dict[str, Any]:
    summary = _trend_measure_summary(rows)
    times = [
        row["time_ms"]
        for row in rows
        if isinstance(row.get("time_ms"), int) and not isinstance(row.get("time_ms"), bool)
    ]
    return {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "covered_start_ms": min(times) if times else None,
        "covered_end_ms": max(times) if times else None,
        "eligible_sessions": len(rows),
        **summary,
    }


def _trend_delta(
    earlier: Mapping[str, Any], later: Mapping[str, Any], field: str, *, unit: str
) -> dict[str, Any]:
    left = (
        earlier.get("measures", {}).get(field)
        if field != "failure_share"
        else earlier.get("failure_share")
    )
    right = (
        later.get("measures", {}).get(field)
        if field != "failure_share"
        else later.get("failure_share")
    )
    left_value = left.get("value") if isinstance(left, Mapping) else None
    right_value = right.get("value") if isinstance(right, Mapping) else None
    left_samples = (
        (
            left.get("observed_sessions", 0)
            if field != "failure_share"
            else left.get("sample_size", 0)
        )
        if isinstance(left, Mapping)
        else 0
    )
    right_samples = (
        (
            right.get("observed_sessions", 0)
            if field != "failure_share"
            else right.get("sample_size", 0)
        )
        if isinstance(right, Mapping)
        else 0
    )
    if (
        isinstance(left_value, (int, float))
        and not isinstance(left_value, bool)
        and isinstance(right_value, (int, float))
        and not isinstance(right_value, bool)
        and left_samples > 0
        and right_samples > 0
    ):
        delta = (
            (right_value - left_value) * 100
            if field == "failure_share"
            else right_value - left_value
        )
        return {
            "state": (
                TokenMeasureState.MEASURED_ZERO.value
                if delta == 0
                else TokenMeasureState.MEASURED.value
            ),
            "value": delta,
            "unit": unit,
            "reason": None,
            "earlier_samples": left_samples,
            "later_samples": right_samples,
        }
    reason = (
        "earlier interval has no observed values"
        if not left_samples or left_value is None
        else "later interval has no observed values"
    )
    return {
        "state": "no_observations",
        "value": None,
        "unit": unit,
        "reason": reason,
        "earlier_samples": left_samples,
        "later_samples": right_samples,
    }


def _trend_view(
    all_sessions: Sequence[Mapping[str, Any]],
    selected_sessions: Sequence[Mapping[str, Any]],
    *,
    levels: Sequence[str | None],
    window: str,
    generated_at_ms: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    level_set = set(levels)
    population = [row for row in all_sessions if row.get("level") in level_set]
    future_count = sum(
        isinstance(row.get("time_ms"), int)
        and not isinstance(row.get("time_ms"), bool)
        and row["time_ms"] > generated_at_ms
        for row in population
    )
    untimed_count = sum(row.get("time_ms") is None for row in population)
    timed = [
        row
        for row in population
        if isinstance(row.get("time_ms"), int)
        and not isinstance(row.get("time_ms"), bool)
        and row["time_ms"] <= generated_at_ms
    ]
    days = next((item.days for item in WINDOWS if item.key == window), None)
    start_ms = (
        generated_at_ms - days * DAY_MS
        if days is not None
        else min((row["time_ms"] for row in timed), default=generated_at_ms)
    )
    end_ms = generated_at_ms
    eligible = [row for row in timed if start_ms <= row["time_ms"] <= end_ms]
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in eligible:
        groups[_trend_group_key(row)].append(row)

    day_starts = list(range(start_ms // DAY_MS * DAY_MS, end_ms // DAY_MS * DAY_MS + 1, DAY_MS))
    rows_out: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    midpoint = start_ms + (end_ms - start_ms) // 2
    for identity, group_rows in sorted(
        groups.items(), key=lambda item: tuple(str(v) for v in item[0])
    ):
        skill, recipe, step, harness, provider, model = identity
        group_population = [row for row in population if _trend_group_key(row) == identity]
        group_future = sum(
            isinstance(row.get("time_ms"), int)
            and not isinstance(row.get("time_ms"), bool)
            and row["time_ms"] > generated_at_ms
            for row in group_population
        )
        group_untimed = sum(row.get("time_ms") is None for row in group_population)
        group_retained = min(
            (
                row["time_ms"]
                for row in group_population
                if isinstance(row.get("time_ms"), int)
                and not isinstance(row.get("time_ms"), bool)
                and row["time_ms"] <= generated_at_ms
            ),
            default=None,
        )
        for day_start in day_starts:
            bucket_start = max(day_start, start_ms)
            bucket_end = min(day_start + DAY_MS, end_ms)
            bucket = [
                row
                for row in group_rows
                if bucket_start <= row["time_ms"] < day_start + DAY_MS
                and row["time_ms"] <= bucket_end
            ]
            summary = _trend_measure_summary(bucket)
            rows_out.append(
                {
                    "key": (
                        f"{window}:{skill}:{recipe}:{step}:"
                        f"{harness}:{provider}:{model}:{day_start}"
                    ),
                    "time_ms": bucket_start,
                    "bucket_end_ms": bucket_end,
                    "day": datetime.fromtimestamp(day_start / 1000, UTC).date().isoformat(),
                    "skill": skill,
                    "recipe": recipe,
                    "step": step,
                    "harness": harness,
                    "provider": provider,
                    "model": model,
                    **summary,
                    "eligible_sessions": len(bucket),
                    "untimed_sessions": group_untimed,
                    "future_sessions": group_future,
                    "retained_from_ms": group_retained,
                    "window_start_ms": start_ms,
                    "window_end_ms": end_ms,
                }
            )
        earlier_rows = [row for row in group_rows if start_ms <= row["time_ms"] < midpoint]
        later_rows = [row for row in group_rows if midpoint <= row["time_ms"] <= end_ms]
        earlier = _interval_summary(earlier_rows, start_ms=start_ms, end_ms=midpoint)
        later = _interval_summary(later_rows, start_ms=midpoint, end_ms=end_ms)
        deltas = {
            field: _trend_delta(
                earlier,
                later,
                field,
                unit="tokens/session",
            )
            for field in TOKEN_FIELDS
        }
        deltas["failure_share"] = _trend_delta(
            earlier, later, "failure_share", unit="percentage_points"
        )
        comparisons.append(
            {
                "skill": skill,
                "recipe": recipe,
                "step": step,
                "harness": harness,
                "provider": provider,
                "model": model,
                "window": window,
                "earlier": earlier,
                "later": later,
                "deltas": deltas,
                "untimed_sessions": group_untimed,
                "future_sessions": group_future,
                "retained_from_ms": group_retained,
            }
        )
    rows_out.sort(
        key=lambda row: (
            row["time_ms"],
            row["harness"],
            row["provider"],
            row["skill"] or "",
            row["step"] or "",
        )
    )
    metadata = {
        "window_start_ms": start_ms,
        "window_end_ms": end_ms,
        "retained_from_ms": min((row["time_ms"] for row in timed), default=None),
        "untimed_sessions": untimed_count,
        "future_sessions": future_count,
        "eligible_sessions": len(eligible),
        "selected_sessions": len(selected_sessions),
    }
    return rows_out, comparisons, metadata
