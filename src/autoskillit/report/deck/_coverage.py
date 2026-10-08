from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from autoskillit.core import (
    MeasureRecord,
    SourcePair,
    TokenMeasure,
    TokenMeasureState,
)

from ._context import _parent_context_metrics
from ._coverage_support import (
    _categorical_measure,
    _coverage_row,
)
from ._errors import _selected_tool_events
from ._measure_helpers import (
    TOKEN_FIELDS,
    _aggregate_fields,
    _decode_measure,
    _measure_records,
    _outcome_record,
    _ratio_field,
    _ratio_state_measure,
    _source_pair,
)
from ._view_common import _latest_time, _session_identity, _time_in_window

_Identity = tuple[str, str, str | None, str | None, str | None]


def _time_basis(
    observations: Sequence[Mapping[str, Any]],
    owners: Sequence[Mapping[str, Any]],
) -> tuple[int | None, str]:
    if observations:
        time_ms = _latest_time(observations)
        return time_ms, "observation_timestamp" if time_ms is not None else "untimed_observation"
    return _latest_time(owners), "owner_session_eligibility"


def _session_coverage_rows(
    identity: _Identity,
    pair: SourcePair,
    level: str | None,
    sessions: Sequence[Mapping[str, Any]],
    *,
    session_key: str | None,
) -> list[dict[str, Any]]:
    skill, recipe, step = identity[2:]
    eligible = len(sessions)
    time_ms = _latest_time(sessions)
    rows: list[dict[str, Any]] = []
    session_fields = _aggregate_fields(
        _measure_records(sessions), TOKEN_FIELDS, eligible_count=eligible
    )
    for field in TOKEN_FIELDS:
        rows.append(
            _coverage_row(
                pair=pair,
                model=None,
                level=level,
                skill=skill,
                recipe=recipe,
                step=step,
                population="session",
                field=field,
                measure=session_fields[field],
                eligible_count=eligible,
                observation_count=eligible,
                source="indexed session token usage at owner session time",
                time_ms=time_ms,
                session_key=session_key,
            )
        )

    model_measure, model_counts, model_reason = _categorical_measure(
        [row.get("model") for row in sessions]
    )
    rows.append(
        _coverage_row(
            pair=pair,
            model=None,
            level=level,
            skill=skill,
            recipe=recipe,
            step=step,
            population="session",
            field="resolved_model",
            measure=model_measure,
            eligible_count=eligible,
            observation_count=eligible,
            source="indexed session model at owner session time",
            time_ms=time_ms,
            reason=model_reason,
            state_counts=model_counts,
            session_key=session_key,
        )
    )

    outcomes = [_outcome_record(row) for row in sessions]
    outcome = _aggregate_fields(outcomes, ("failure",), eligible_count=eligible)["failure"]
    known = [record for record in outcomes if record.measures["known_attempt"].value == 1]
    outcome_ratio = _ratio_field(known, "failure", "known_attempt", eligible_count=eligible)
    rows.append(
        _coverage_row(
            pair=pair,
            model=None,
            level=level,
            skill=skill,
            recipe=recipe,
            step=step,
            population="session",
            field="session_outcomes",
            measure=outcome,
            eligible_count=eligible,
            observation_count=eligible,
            source="indexed session success outcome at owner session time",
            time_ms=time_ms,
            reason=(
                f"{outcome_ratio['unknown_runs']} session outcomes are unknown"
                if outcome_ratio.get("unknown_runs")
                else None
            ),
            session_key=session_key,
        )
    )
    return rows


def _turn_records(
    rows: Sequence[Mapping[str, Any]], pair: SourcePair, eligible_count: int
) -> list[MeasureRecord]:
    records = []
    for row in rows:
        measures = {field: _decode_measure(row, field) for field in TOKEN_FIELDS}
        window = row.get("context_window_tokens")
        measures["context_window_tokens"] = (
            TokenMeasure.observed(window)
            if isinstance(window, int) and not isinstance(window, bool) and window > 0
            else TokenMeasure.unknown()
        )
        records.append(MeasureRecord(pair, measures))
    return records


def _turn_measure_rows(
    identity: _Identity,
    pair: SourcePair,
    level: str | None,
    sessions: Sequence[Mapping[str, Any]],
    turns: Sequence[Mapping[str, Any]],
    *,
    session_key: str | None,
) -> list[dict[str, Any]]:
    skill, recipe, step = identity[2:]
    eligible = len(sessions)
    time_ms, time_basis = _time_basis(turns, sessions)
    records = _turn_records(turns, pair, eligible)
    aggregates = _aggregate_fields(
        records, (*TOKEN_FIELDS, "context_window_tokens"), eligible_count=eligible
    )
    rows = []
    for field in (*TOKEN_FIELDS, "context_window_tokens"):
        reasons = sorted(
            {
                str(row.get("turn_usage_reason"))
                for row in sessions
                if isinstance(row.get("turn_usage_reason"), str)
            }
        )
        rows.append(
            _coverage_row(
                pair=pair,
                model=None,
                level=level,
                skill=skill,
                recipe=recipe,
                step=step,
                population="turn",
                field=field,
                measure=aggregates[field],
                eligible_count=eligible,
                observation_count=len(records),
                source=(
                    f"turn_usage.jsonl model window at {time_basis.replace('_', ' ')}"
                    if field == "context_window_tokens"
                    else f"turn_usage.jsonl at {time_basis.replace('_', ' ')}"
                ),
                time_ms=time_ms,
                reason="; ".join(reasons) if reasons else None,
                session_key=session_key,
            )
        )
    return rows


def _ledger_row(
    identity: _Identity,
    pair: SourcePair,
    level: str | None,
    sessions: Sequence[Mapping[str, Any]],
    turns: Sequence[Mapping[str, Any]],
    all_sessions: Sequence[Mapping[str, Any]],
    *,
    session_key: str | None,
) -> dict[str, Any]:
    time_ms, time_basis = _time_basis(turns, sessions)
    ledger_rows: dict[str, Mapping[str, Any]] = {
        key: row for row in sessions if isinstance((key := row.get("key")), str)
    }
    owners: dict[str, Mapping[str, Any]] = {
        key: row for row in all_sessions if isinstance((key := row.get("key")), str)
    }
    for turn in turns:
        owner_key = turn.get("session_key")
        if not isinstance(owner_key, str) or owner_key in ledger_rows:
            continue
        owner = owners.get(owner_key, turn)
        ledger_rows[owner_key] = {
            **dict(owner),
            "turn_usage_state": "observed",
            "turn_usage_reason": None,
        }
    records = []
    for row in ledger_rows.values():
        state = row.get("turn_usage_state")
        measure = (
            TokenMeasure.observed(1)
            if state == "observed"
            else TokenMeasure.unavailable()
            if state == "unavailable"
            else TokenMeasure.unknown()
        )
        records.append(MeasureRecord(pair, {"ledger": measure}))
    ledger = _aggregate_fields(records, ("ledger",), eligible_count=len(sessions))["ledger"]
    reasons = sorted(
        {
            str(row.get("turn_usage_reason"))
            for row in sessions
            if isinstance(row.get("turn_usage_reason"), str)
        }
    )
    return _coverage_row(
        pair=pair,
        model=None,
        level=level,
        skill=identity[2],
        recipe=identity[3],
        step=identity[4],
        population="turn",
        field="turn_series_coverage",
        measure=ledger,
        eligible_count=len(sessions),
        observation_count=len(records),
        source=f"turn_usage_state and turn_usage_reason at {time_basis.replace('_', ' ')}",
        time_ms=time_ms,
        reason="; ".join(reasons) if reasons else None,
        session_key=session_key,
    )


def _occupancy_rows(
    identity: _Identity,
    pair: SourcePair,
    level: str | None,
    sessions: Sequence[Mapping[str, Any]],
    turns: Sequence[Mapping[str, Any]],
    *,
    session_key: str | None,
) -> list[dict[str, Any]]:
    skill, recipe, step = identity[2:]
    eligible = len(sessions)
    time_ms, time_basis = _time_basis(turns, sessions)
    fraction_records = []
    disagreement_records = []
    ratio_records = []
    for row in turns:
        cache_read = _decode_measure(row, "cache_read_tokens")
        raw_window = row.get("context_window_tokens")
        denominator = (
            TokenMeasure.observed(raw_window)
            if isinstance(raw_window, int) and not isinstance(raw_window, bool) and raw_window > 0
            else TokenMeasure.unknown()
        )
        ratio_record = MeasureRecord(
            pair,
            {"cache_read_tokens": cache_read, "context_window_tokens": denominator},
        )
        ratio = _ratio_field(
            [ratio_record],
            "cache_read_tokens",
            "context_window_tokens",
            eligible_count=1,
        )
        fraction_records.append(
            MeasureRecord(
                pair,
                {"context_fraction": _ratio_state_measure(TokenMeasureState(ratio["state"]))},
            )
        )
        ratio_records.append(ratio_record)
        disagreement = row.get("fraction_disagreement")
        disagreement_measure = (
            TokenMeasure.observed(1)
            if disagreement is True
            else TokenMeasure.observed(0)
            if disagreement is False
            else TokenMeasure.unknown()
        )
        disagreement_records.append(
            MeasureRecord(pair, {"fraction_disagreement": disagreement_measure})
        )
    fraction_states = _aggregate_fields(
        fraction_records, ("context_fraction",), eligible_count=eligible
    )["context_fraction"]
    pooled = _ratio_field(
        ratio_records, "cache_read_tokens", "context_window_tokens", eligible_count=eligible
    )
    disagreement = _aggregate_fields(
        disagreement_records, ("fraction_disagreement",), eligible_count=eligible
    )["fraction_disagreement"]
    reason = (
        "stored context_fraction disagrees with the recomputed per-turn ratio"
        if isinstance(disagreement.get("value"), int) and disagreement["value"] > 0
        else None
    )
    rows = [
        _coverage_row(
            pair=pair,
            model=None,
            level=level,
            skill=skill,
            recipe=recipe,
            step=step,
            population="turn",
            field="context_fraction",
            measure={
                "state": pooled.get("state"),
                "value": pooled.get("value"),
                "state_counts": fraction_states["state_counts"],
            },
            eligible_count=eligible,
            observation_count=len(turns),
            source=(
                "per-turn cache_read_tokens/context_window_tokens ratio at "
                f"{time_basis.replace('_', ' ')}"
            ),
            time_ms=time_ms,
            session_key=session_key,
        ),
        _coverage_row(
            pair=pair,
            model=None,
            level=level,
            skill=skill,
            recipe=recipe,
            step=step,
            population="turn",
            field="context_fraction_validation",
            measure=disagreement,
            eligible_count=eligible,
            observation_count=len(turns),
            source=(
                "stored context_fraction compared with recomputed ratio at "
                f"{time_basis.replace('_', ' ')}"
            ),
            time_ms=time_ms,
            reason=reason,
            session_key=session_key,
        ),
    ]
    return rows


def _tool_coverage_rows(
    identity: _Identity,
    pair: SourcePair,
    level: str | None,
    sessions: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    *,
    session_key: str | None,
) -> list[dict[str, Any]]:
    skill, recipe, step = identity[2:]
    eligible = len(sessions)
    time_ms, time_basis = _time_basis(events, sessions)
    outcome_records = [_outcome_record(event) for event in events]
    outcomes = _aggregate_fields(outcome_records, ("failure",), eligible_count=eligible)["failure"]
    attribution_records = [
        MeasureRecord(
            pair,
            {
                "skill_step": (
                    TokenMeasure.observed(1)
                    if isinstance(event.get("skill"), str) and isinstance(event.get("step"), str)
                    else TokenMeasure.unknown()
                )
            },
        )
        for event in events
    ]
    attribution = _aggregate_fields(attribution_records, ("skill_step",), eligible_count=eligible)[
        "skill_step"
    ]
    return [
        _coverage_row(
            pair=pair,
            model=None,
            level=level,
            skill=skill,
            recipe=recipe,
            step=step,
            population="tool_result",
            field="tool_errors",
            measure=outcomes,
            eligible_count=eligible,
            observation_count=len(events),
            session_key=session_key,
            source=(
                f"indexed tool_result success and error_type at {time_basis.replace('_', ' ')}"
            ),
            time_ms=time_ms,
            reason=(
                "no indexed tool_result events; absence does not establish zero errors"
                if not events
                else None
            ),
        ),
        _coverage_row(
            pair=pair,
            model=None,
            level=level,
            skill=skill,
            recipe=recipe,
            step=step,
            population="tool_result",
            field="tool_skill_step_attribution",
            measure=attribution,
            eligible_count=eligible,
            observation_count=len(events),
            session_key=session_key,
            source=(
                "tool session_key joined to indexed session skill and step at "
                f"{time_basis.replace('_', ' ')}"
            ),
            time_ms=time_ms,
            reason="no attributed tool events" if not events else None,
        ),
    ]


def _parent_time_basis(
    item: Mapping[str, Any], sessions: Sequence[Mapping[str, Any]]
) -> tuple[int | None, str]:
    provenance = item.get("provenance", ())
    timestamped = [
        span
        for span in provenance
        if isinstance(span, Mapping)
        and isinstance(span.get("timestamp_ms"), int)
        and not isinstance(span.get("timestamp_ms"), bool)
    ]
    if timestamped:
        return _latest_time(timestamped, "timestamp_ms"), "parent_span_timestamp"
    if item.get("untimed_excluded"):
        return None, "untimed_observation_excluded"
    return _latest_time(sessions), "owner_session_eligibility"


def _parent_coverage_rows(
    identity: _Identity,
    pair: SourcePair,
    level: str | None,
    sessions: Sequence[Mapping[str, Any]],
    metrics: Sequence[Mapping[str, Any]],
    *,
    session_key: str | None,
) -> list[dict[str, Any]]:
    rows = []
    for item in metrics:
        time_ms, time_basis = _parent_time_basis(item, sessions)
        for field, measure in (
            ("parent_prompt_tokens", item["prompt"]),
            ("subagent_return_tokens", item["returned"]),
        ):
            reasons = sorted(
                {
                    str(span["reason"])
                    for span in item.get("provenance", ())
                    if isinstance(span.get("reason"), str)
                }
            )
            rows.append(
                _coverage_row(
                    pair=pair,
                    model=item.get("model"),
                    level=level,
                    skill=identity[2],
                    recipe=identity[3],
                    step=identity[4],
                    population="parent_context",
                    field=field,
                    measure=measure,
                    eligible_count=item.get("eligible_parent_sessions", len(sessions)),
                    observation_count=measure.get("observation_count", 0),
                    source=(f"indexed parent_context_spans at {time_basis.replace('_', ' ')}"),
                    time_ms=time_ms,
                    reason=(
                        "; ".join(reasons)
                        if reasons
                        else "no verified parent-context invocation spans"
                        if measure.get("state") == "no_observations"
                        else None
                    ),
                    session_key=session_key,
                )
            )
    return rows


def _identity_coverage_rows(
    identity: _Identity,
    sessions: Sequence[Mapping[str, Any]],
    turns: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    parent_metrics: Sequence[Mapping[str, Any]],
    all_sessions: Sequence[Mapping[str, Any]],
    *,
    session_key: str | None,
) -> list[dict[str, Any]]:
    pair = SourcePair(identity[0], identity[1])
    level = next(
        (
            row.get("level")
            for row in (*sessions, *turns, *events)
            if isinstance(row.get("level"), str)
        ),
        next(
            (item.get("level") for item in parent_metrics if isinstance(item.get("level"), str)),
            None,
        ),
    )
    return [
        *_session_coverage_rows(identity, pair, level, sessions, session_key=session_key),
        *_turn_measure_rows(identity, pair, level, sessions, turns, session_key=session_key),
        _ledger_row(
            identity,
            pair,
            level,
            sessions,
            turns,
            all_sessions,
            session_key=session_key,
        ),
        *_occupancy_rows(identity, pair, level, sessions, turns, session_key=session_key),
        *_tool_coverage_rows(identity, pair, level, sessions, events, session_key=session_key),
        *_parent_coverage_rows(
            identity, pair, level, sessions, parent_metrics, session_key=session_key
        ),
    ]


def _parent_identity(item: Mapping[str, Any]) -> _Identity:
    harness = item.get("harness")
    provider = item.get("provider")
    return (
        harness if isinstance(harness, str) and harness else "unknown",
        provider if isinstance(provider, str) and provider else "unknown",
        item.get("skill") if isinstance(item.get("skill"), str) else None,
        item.get("recipe") if isinstance(item.get("recipe"), str) else None,
        item.get("step") if isinstance(item.get("step"), str) else None,
    )


def _unattributed_tool_rows(
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not events:
        return []
    groups: dict[SourcePair, list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        groups[_source_pair(event)].append(event)
    rows = []
    for pair, pair_events in groups.items():
        time_ms = _latest_time(pair_events)
        outcome_records = [_outcome_record(event) for event in pair_events]
        outcome = _aggregate_fields(
            outcome_records, ("failure",), eligible_count=len(pair_events)
        )["failure"]
        attribution_records = [
            MeasureRecord(pair, {"skill_step": TokenMeasure.unavailable()}) for _ in pair_events
        ]
        attribution = _aggregate_fields(
            attribution_records, ("skill_step",), eligible_count=len(pair_events)
        )["skill_step"]
        for field, field_measure, source in (
            ("tool_errors", outcome, "indexed unattributed tool_result outcomes"),
            (
                "tool_skill_step_attribution",
                attribution,
                "tool_result rows without an exact owning session_key",
            ),
        ):
            row = _coverage_row(
                pair=pair,
                model=None,
                level=None,
                skill=None,
                recipe=None,
                step=None,
                population="unattributed_tool",
                field=field,
                measure=field_measure,
                eligible_count=len(pair_events),
                observation_count=len(pair_events),
                source=(
                    f"{source} at event timestamp"
                    if time_ms is not None
                    else f"{source} as an untimed observation"
                ),
                time_ms=time_ms,
                reason="tool event has no exact indexed owner; no pair or skill/step is inferred",
                state_counts=field_measure["state_counts"],
            )
            rows.append(
                {
                    **row,
                    "key": f"unattributed_tool:{pair.harness}:{pair.provider}:{field}",
                }
            )
    return rows


def _coverage_selection(
    eligible_sessions: Sequence[Mapping[str, Any]],
    all_sessions: Sequence[Mapping[str, Any]],
    turns: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    children: Sequence[Mapping[str, Any]],
    *,
    levels: Sequence[str | None],
    window: str,
    generated_at_ms: int,
    session_key: str | None,
) -> tuple[
    Sequence[Mapping[str, Any]],
    list[Mapping[str, Any]],
    list[Mapping[str, Any]],
    Sequence[Mapping[str, Any]],
]:
    level_set = set(levels)
    if session_key is not None:
        eligible_sessions = [
            row
            for row in all_sessions
            if row.get("key") == session_key
            and row.get("level") in level_set
            and _time_in_window(row.get("time_ms"), window, generated_at_ms=generated_at_ms)
        ]
        children = [row for row in children if row.get("parent_session_key") == session_key]
    selected_turns = [
        row
        for row in turns
        if row.get("level") in level_set
        and _time_in_window(row.get("time_ms"), window, generated_at_ms=generated_at_ms)
    ]
    selected_tools = _selected_tool_events(
        tools,
        all_sessions,
        levels=levels,
        window=window,
        generated_at_ms=generated_at_ms,
    )
    if session_key is not None:
        selected_turns = [row for row in selected_turns if row.get("session_key") == session_key]
        selected_tools = [row for row in selected_tools if row.get("session_key") == session_key]
    return eligible_sessions, selected_turns, selected_tools, children


def _coverage_rows(
    eligible_sessions: Sequence[Mapping[str, Any]],
    all_sessions: Sequence[Mapping[str, Any]],
    turns: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    children: Sequence[Mapping[str, Any]],
    *,
    levels: Sequence[str | None],
    window: str,
    generated_at_ms: int,
    session_key: str | None = None,
) -> list[dict[str, Any]]:
    level_set = set(levels)
    eligible_sessions, selected_turns, selected_tools, children = _coverage_selection(
        eligible_sessions,
        all_sessions,
        turns,
        tools,
        children,
        levels=levels,
        window=window,
        generated_at_ms=generated_at_ms,
        session_key=session_key,
    )

    sessions_by_identity: dict[_Identity, list[Mapping[str, Any]]] = defaultdict(list)
    turns_by_identity: dict[_Identity, list[Mapping[str, Any]]] = defaultdict(list)
    events_by_identity: dict[_Identity, list[Mapping[str, Any]]] = defaultdict(list)
    for row in eligible_sessions:
        sessions_by_identity[_session_identity(row)].append(row)
    for row in selected_turns:
        turns_by_identity[_session_identity(row)].append(row)
    for row in selected_tools:
        if row.get("attribution_state") == "attributed":
            events_by_identity[_session_identity(row)].append(row)

    parent_metrics = _parent_context_metrics(
        eligible_sessions,
        all_sessions,
        children,
        levels=levels,
        window=window,
        generated_at_ms=generated_at_ms,
    )
    parents_by_identity: dict[_Identity, list[Mapping[str, Any]]] = defaultdict(list)
    for item in parent_metrics:
        parents_by_identity[_parent_identity(item)].append(item)
    identities = (
        set(sessions_by_identity)
        | set(turns_by_identity)
        | set(events_by_identity)
        | set(parents_by_identity)
    )
    ordered = sorted(
        identities, key=lambda identity: tuple(str(value or "") for value in identity)
    )
    rows = []
    for identity in ordered:
        rows.extend(
            _identity_coverage_rows(
                identity,
                sessions_by_identity.get(identity, ()),
                turns_by_identity.get(identity, ()),
                events_by_identity.get(identity, ()),
                parents_by_identity.get(identity, ()),
                all_sessions,
                session_key=session_key,
            )
        )
    if session_key is None and None in level_set:
        rows.extend(
            _unattributed_tool_rows(
                [
                    event
                    for event in selected_tools
                    if event.get("attribution_state") == "unattributed"
                ]
            )
        )
    return rows
