from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from autoskillit.core import MeasureRecord, SourcePair, TokenMeasure

from ._measure_helpers import (
    _aggregate_fields,
    _outcome_summary,
    _ratio_field,
    _source_pair,
)
from ._view_common import _latest_time, _session_identity, _time_in_window

_ErrorIdentity = tuple[str, str, str | None, str | None, str | None]


def _selected_tool_events(
    tools: Sequence[Mapping[str, Any]],
    all_sessions: Sequence[Mapping[str, Any]],
    *,
    levels: Sequence[str | None],
    window: str,
    generated_at_ms: int,
) -> list[Mapping[str, Any]]:
    level_set = set(levels)
    owners: dict[str, Mapping[str, Any]] = {
        key: row
        for row in all_sessions
        if isinstance((key := row.get("key")), str) and row.get("level") in level_set
    }
    selected: list[Mapping[str, Any]] = []
    for raw in tools:
        if not _time_in_window(raw.get("time_ms"), window, generated_at_ms=generated_at_ms):
            continue
        owner_key = raw.get("session_key")
        owner = owners.get(owner_key) if isinstance(owner_key, str) else None
        if owner is None:
            event_pair = _source_pair(raw)
            selected.append(
                {
                    **dict(raw),
                    "session_key": None,
                    "harness": event_pair.harness,
                    "provider": event_pair.provider,
                    "skill": None,
                    "recipe": None,
                    "step": None,
                    "level": None,
                    "attribution_state": "unattributed",
                }
            )
        else:
            owner_pair = _source_pair(owner)
            selected.append(
                {
                    **dict(raw),
                    "harness": owner_pair.harness,
                    "provider": owner_pair.provider,
                    "skill": owner.get("skill"),
                    "recipe": owner.get("recipe"),
                    "step": owner.get("step"),
                    "level": owner.get("level"),
                    "attribution_state": "attributed",
                }
            )
    return selected


def _session_symptoms(
    rows: Sequence[Mapping[str, Any]],
) -> list[tuple[str, list[Mapping[str, Any]]]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("success") is False:
            symptom = next(
                (
                    value
                    for field in ("adjudication_reason", "adjudication_subtype", "subtype")
                    if isinstance((value := row.get(field)), str) and value
                ),
                "Unknown symptom",
            )
            groups[symptom].append(row)
        elif row.get("success") is None:
            groups["Outcome unknown"].append(row)
    return list(groups.items())


def _tool_symptoms(
    events: Sequence[Mapping[str, Any]],
) -> list[tuple[str, list[Mapping[str, Any]]]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        error_type = event.get("error_type")
        if event.get("success") is not False and not isinstance(error_type, str):
            continue
        symptom = error_type if isinstance(error_type, str) else "Unknown symptom"
        groups[symptom].append(event)
    return list(groups.items())


def _session_error_row(
    identity: _ErrorIdentity,
    symptom: str,
    affected: Sequence[Mapping[str, Any]],
    eligible: Sequence[Mapping[str, Any]],
    tool_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    harness, provider, skill, recipe, step = identity
    pair = SourcePair(harness, provider)
    failure_records = [
        MeasureRecord(
            pair,
            {
                "failures": (
                    TokenMeasure.observed(1)
                    if row.get("success") is False
                    else TokenMeasure.unknown()
                )
            },
        )
        for row in affected
    ]
    failure_count = _aggregate_fields(
        failure_records, ("failures",), eligible_count=len(eligible)
    )["failures"]
    outcome = _outcome_summary(eligible)
    session_keys = sorted(str(key) for row in affected if isinstance((key := row.get("key")), str))
    return {
        "key": f"session:{harness}:{provider}:{skill}:{recipe}:{step}:{symptom}",
        "time_ms": _latest_time(affected),
        "session_key": session_keys[0] if len(session_keys) == 1 else None,
        "session_keys": session_keys,
        "harness": harness,
        "provider": provider,
        "skill": skill,
        "recipe": recipe,
        "step": step,
        "level": affected[0].get("level"),
        "population": "session_outcome" if symptom == "Outcome unknown" else "session_failure",
        "symptom": symptom,
        "failures": failure_count,
        "failure_rate": outcome["failure_rate"],
        "eligible_count": outcome["eligible_count"],
        "observed_count": outcome["observed_count"],
        "unknown_count": outcome["unknown_count"],
        "tool_result_event_count": len(tool_events),
        "tool_event_coverage": _tool_event_coverage(eligible, tool_events),
        "attribution_state": "attributed",
    }


def _session_error_rows(
    sessions: Sequence[Mapping[str, Any]],
    tool_groups: Mapping[_ErrorIdentity, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    groups: dict[_ErrorIdentity, list[Mapping[str, Any]]] = defaultdict(list)
    for row in sessions:
        groups[_session_identity(row)].append(row)
    return [
        _session_error_row(
            identity,
            symptom,
            affected,
            eligible,
            tool_groups.get(identity, ()),
        )
        for identity, eligible in groups.items()
        for symptom, affected in _session_symptoms(eligible)
    ]


def _tool_error_row(
    identity: _ErrorIdentity,
    symptom: str,
    affected: Sequence[Mapping[str, Any]],
    eligible_events: Sequence[Mapping[str, Any]],
    eligible_sessions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    harness, provider, skill, recipe, step = identity
    pair = SourcePair(harness, provider)
    failure_records = [
        MeasureRecord(
            pair,
            {
                "failures": (
                    TokenMeasure.observed(1)
                    if event.get("success") is False
                    else TokenMeasure.unknown()
                    if event.get("success") is None
                    else TokenMeasure.not_applicable()
                )
            },
        )
        for event in affected
    ]
    failure_count = _aggregate_fields(
        failure_records, ("failures",), eligible_count=len(eligible_events)
    )["failures"]
    outcome = _outcome_summary(eligible_events)
    session_keys = sorted(
        {str(key) for event in affected if isinstance((key := event.get("session_key")), str)}
    )
    return {
        "key": f"tool:{harness}:{provider}:{skill}:{recipe}:{step}:{symptom}",
        "time_ms": _latest_time(affected),
        "session_key": session_keys[0] if len(session_keys) == 1 else None,
        "session_keys": session_keys,
        "harness": harness,
        "provider": provider,
        "skill": skill,
        "recipe": recipe,
        "step": step,
        "level": affected[0].get("level"),
        "population": "tool_failure",
        "symptom": symptom,
        "failures": failure_count,
        "failure_rate": outcome["failure_rate"],
        "eligible_count": outcome["eligible_count"],
        "observed_count": outcome["observed_count"],
        "unknown_count": outcome["unknown_count"],
        "tool_result_event_count": len(eligible_events),
        "tool_event_coverage": _tool_event_coverage(eligible_sessions, eligible_events),
        "attribution_state": "attributed",
    }


def _tool_error_rows(
    sessions: Sequence[Mapping[str, Any]],
    tool_groups: Mapping[_ErrorIdentity, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    session_groups: dict[_ErrorIdentity, list[Mapping[str, Any]]] = defaultdict(list)
    for row in sessions:
        session_groups[_session_identity(row)].append(row)
    rows = []
    for identity, eligible_events in tool_groups.items():
        eligible_sessions = session_groups.get(identity, [])
        for symptom, affected in _tool_symptoms(eligible_events):
            rows.append(
                _tool_error_row(
                    identity,
                    symptom,
                    affected,
                    eligible_events,
                    eligible_sessions,
                )
            )
    return rows


def _unattributed_error_rows(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not events:
        return []
    groups: dict[SourcePair, list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        groups[_source_pair(event)].append(event)
    rows = []
    for pair, pair_events in groups.items():
        outcome = _outcome_summary(pair_events)
        for symptom, affected in _tool_symptoms(pair_events):
            records = [
                MeasureRecord(
                    pair,
                    {
                        "failures": TokenMeasure.observed(1)
                        if event.get("success") is False
                        else TokenMeasure.unknown()
                    },
                )
                for event in affected
            ]
            rows.append(
                {
                    "key": f"unattributed:{pair.harness}:{pair.provider}:{symptom}",
                    "time_ms": _latest_time(affected),
                    "session_key": None,
                    "session_keys": [],
                    "harness": pair.harness,
                    "provider": pair.provider,
                    "skill": None,
                    "recipe": None,
                    "step": None,
                    "level": None,
                    "population": "unattributed_tool",
                    "symptom": symptom,
                    "failures": _aggregate_fields(records, ("failures",))["failures"],
                    "failure_rate": outcome["failure_rate"],
                    "eligible_count": len(pair_events),
                    "observed_count": outcome["observed_count"],
                    "unknown_count": outcome["unknown_count"],
                    "tool_result_event_count": len(pair_events),
                    "tool_event_coverage": {
                        "state": "unattributed",
                        "value": None,
                        "sample_size": 0,
                        "excluded_runs": 0,
                        "unknown_runs": len(pair_events),
                        "eligible_count": len(pair_events),
                        "observation_count": len(pair_events),
                    },
                    "attribution_state": "unattributed",
                }
            )
    return rows


def _error_rows(
    sessions: Sequence[Mapping[str, Any]],
    all_sessions: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    *,
    levels: Sequence[str | None],
    window: str,
    generated_at_ms: int,
) -> tuple[list[dict[str, Any]], list[Mapping[str, Any]]]:
    events = _selected_tool_events(
        tools,
        all_sessions,
        levels=levels,
        window=window,
        generated_at_ms=generated_at_ms,
    )
    tool_groups: dict[_ErrorIdentity, list[Mapping[str, Any]]] = defaultdict(list)
    unattributed = []
    for event in events:
        if event.get("attribution_state") == "attributed":
            tool_groups[_session_identity(event)].append(event)
        else:
            unattributed.append(event)
    rows = [
        *_session_error_rows(sessions, tool_groups),
        *_tool_error_rows(sessions, tool_groups),
        *_unattributed_error_rows(unattributed),
    ]
    rows.sort(key=lambda row: (row.get("time_ms") is None, row.get("time_ms") or 0, row["key"]))
    return rows, events


def _tool_event_coverage(
    eligible_sessions: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    event_owners = {key for event in events if isinstance((key := event.get("session_key")), str)}
    records = [
        MeasureRecord(
            _source_pair(session),
            {
                "tool_result_event": (
                    TokenMeasure.observed(1)
                    if session.get("key") in event_owners
                    else TokenMeasure.unknown()
                ),
                "eligible_sessions": TokenMeasure.observed(1),
            },
        )
        for session in eligible_sessions
    ]
    if not records:
        return _ratio_field([], "tool_result_event", "eligible_sessions")
    result = _ratio_field(records, "tool_result_event", "eligible_sessions")
    state_counts = _aggregate_fields(records, ("tool_result_event",))["tool_result_event"][
        "state_counts"
    ]
    return {
        **result,
        "observation_count": state_counts["measured"] + state_counts["measured_zero"],
        "state_counts": state_counts,
    }


def _error_population_metrics(
    sessions: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    session_groups: dict[_ErrorIdentity, list[Mapping[str, Any]]] = defaultdict(list)
    event_groups: dict[_ErrorIdentity, list[Mapping[str, Any]]] = defaultdict(list)
    for row in sessions:
        session_groups[_session_identity(row)].append(row)
    for event in events:
        if event.get("attribution_state") == "attributed":
            event_groups[_session_identity(event)].append(event)
    identities = sorted(
        set(session_groups) | set(event_groups),
        key=lambda identity: tuple(str(value or "") for value in identity),
    )
    result = []
    for identity in identities:
        eligible = session_groups.get(identity, [])
        tool_events = event_groups.get(identity, [])
        result.append(
            {
                "harness": identity[0],
                "provider": identity[1],
                "skill": identity[2],
                "recipe": identity[3],
                "step": identity[4],
                "eligible_sessions": len(eligible),
                "session_outcomes": _outcome_summary(eligible),
                "tool_result_event_count": len(tool_events),
                "tool_event_coverage": _tool_event_coverage(eligible, tool_events),
                "tool_outcomes": _outcome_summary(tool_events),
                "attribution_state": "attributed",
            }
        )
    unattributed_groups: dict[SourcePair, list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        if event.get("attribution_state") == "unattributed":
            unattributed_groups[_source_pair(event)].append(event)
    for pair, unattributed in unattributed_groups.items():
        result.append(
            {
                "harness": pair.harness,
                "provider": pair.provider,
                "skill": None,
                "recipe": None,
                "step": None,
                "eligible_sessions": 0,
                "session_outcomes": _outcome_summary([]),
                "tool_result_event_count": len(unattributed),
                "tool_event_coverage": _tool_event_coverage([], unattributed),
                "tool_outcomes": _outcome_summary(unattributed),
                "attribution_state": "unattributed",
            }
        )
    return result
