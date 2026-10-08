from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from autoskillit.core import (
    MeasureRecord,
    SourcePair,
    TokenMeasure,
    TokenMeasureState,
)

from ._measure_helpers import (
    TOKEN_FIELDS,
    _aggregate_fields,
    _decode_measure,
    _ratio_field,
    _source_pair,
)
from ._view_common import _time_in_window, _timestamp_ms

_SpanGroupKey = tuple[str, str, str | None, str | None, str | None, str | None]


def _context_turn_rows(
    sessions: Sequence[Mapping[str, Any]],
    turns: Sequence[Mapping[str, Any]],
    requests: Sequence[Mapping[str, Any]],
    *,
    generated_at_ms: int,
) -> list[dict[str, Any]]:
    parents: dict[str, Mapping[str, Any]] = {
        key: row for row in sessions if isinstance((key := row.get("key")), str)
    }
    request_lookup: dict[tuple[str, str], Mapping[str, Any]] = {
        (session_key, request_id): row
        for row in requests
        if isinstance((session_key := row.get("session_key")), str)
        and isinstance((request_id := row.get("request_id")), str)
    }
    out: list[dict[str, Any]] = []
    turn_owners: set[str] = set()
    for raw in turns:
        owner_key = raw.get("session_key")
        if not isinstance(owner_key, str):
            continue
        turn_owners.add(owner_key)
        parent = parents.get(owner_key)
        request_id = raw.get("request_id")
        request = (
            request_lookup.get((owner_key, request_id)) if isinstance(request_id, str) else None
        )
        pair = _source_pair(raw)
        cache_read = _decode_measure(raw, "cache_read_tokens")
        window = raw.get("context_window_tokens")
        denominator = (
            TokenMeasure.observed(window)
            if isinstance(window, int) and not isinstance(window, bool) and window > 0
            else TokenMeasure.unknown()
        )
        ratio = _ratio_field(
            [
                MeasureRecord(
                    pair,
                    {
                        "cache_read_tokens": cache_read,
                        "context_window_tokens": denominator,
                    },
                )
            ],
            "cache_read_tokens",
            "context_window_tokens",
            eligible_count=1,
        )
        stored_fraction = raw.get("context_fraction")
        disagreement = (
            not math.isclose(stored_fraction, ratio["value"], rel_tol=1e-6, abs_tol=1e-9)
            if isinstance(stored_fraction, (float, int))
            and not isinstance(stored_fraction, bool)
            and ratio["value"] is not None
            else None
        )
        out.append(
            {
                **dict(raw),
                "kind": "turn",
                "owner_time_ms": parent.get("time_ms") if parent else None,
                "level": raw.get("level", parent.get("level") if parent else None),
                "skill": raw.get("skill", parent.get("skill") if parent else None),
                "recipe": raw.get("recipe", parent.get("recipe") if parent else None),
                "step": raw.get("step", parent.get("step") if parent else None),
                "context_fraction_percent": {
                    "state": ratio["state"],
                    "value": ratio["value"] * 100 if ratio["value"] is not None else None,
                    "sample_size": ratio["sample_size"],
                    "excluded_runs": ratio["excluded_runs"],
                    "unknown_runs": ratio["unknown_runs"],
                },
                "fraction_disagreement": disagreement,
                "turn_usage_state": "observed",
                "turn_usage_reason": parent.get("turn_usage_reason") if parent else None,
                "request_recorded": request is not None,
                "request_model": request.get("model") if request else None,
            }
        )
    for owner_key, parent in parents.items():
        if owner_key in turn_owners:
            continue
        out.append(
            {
                "key": f"{owner_key}:turn-coverage",
                "session_key": owner_key,
                "ordinal": None,
                "time_ms": parent.get("time_ms"),
                "owner_time_ms": parent.get("time_ms"),
                "harness": parent.get("harness"),
                "provider": parent.get("provider"),
                "model": parent.get("model"),
                "skill": parent.get("skill"),
                "recipe": parent.get("recipe"),
                "step": parent.get("step"),
                "level": parent.get("level"),
                "kind": "coverage",
                **{field: TokenMeasure.unknown().to_dict() for field in TOKEN_FIELDS},
                "context_window_tokens": None,
                "context_fraction": None,
                "context_fraction_percent": {
                    "state": "no_observations",
                    "value": None,
                    "sample_size": 0,
                    "excluded_runs": 0,
                    "unknown_runs": 0,
                },
                "fraction_disagreement": None,
                "turn_usage_state": parent.get("turn_usage_state") or "unavailable",
                "turn_usage_reason": parent.get("turn_usage_reason"),
                "request_recorded": False,
                "request_model": None,
            }
        )
    return sorted(
        out,
        key=lambda row: (
            row.get("owner_time_ms") is None,
            row.get("owner_time_ms") or 0,
            row.get("session_key") or "",
            row.get("ordinal") if isinstance(row.get("ordinal"), int) else -1,
        ),
    )


def _group_for_span(
    groups: dict[_SpanGroupKey, dict[str, Any]],
    owner: Mapping[str, Any],
    model: object,
    *,
    harness: object = None,
    provider: object = None,
) -> dict[str, Any]:
    owner_pair = _source_pair(owner)
    pair = SourcePair(
        harness if isinstance(harness, str) and harness else owner_pair.harness,
        provider if isinstance(provider, str) and provider else owner_pair.provider,
    )
    resolved_model = model if isinstance(model, str) and model else None
    skill = owner.get("skill") if isinstance(owner.get("skill"), str) else None
    recipe = owner.get("recipe") if isinstance(owner.get("recipe"), str) else None
    step = owner.get("step") if isinstance(owner.get("step"), str) else None
    key = (pair.harness, pair.provider, resolved_model, skill, recipe, step)
    if key not in groups:
        groups[key] = {
            "pair": pair,
            "model": resolved_model,
            "skill": skill,
            "recipe": recipe,
            "step": step,
            "level": owner.get("level"),
            "spans": {"parent_prompt_tokens": [], "subagent_return_tokens": []},
            "invocations": {},
            "eligible_invocations": set(),
            "eligible_parent_keys": set(),
            "untimed_excluded": 0,
            "provenance": [],
        }
    return groups[key]


def _span_provenance(
    measure: TokenMeasure,
    raw_span: Mapping[str, Any],
    child: Mapping[str, Any],
    parent_key: str,
    child_key: str,
    span_time: int | None,
) -> dict[str, Any]:
    reason = raw_span.get("reason")
    if not isinstance(reason, str) and measure.state not in (
        TokenMeasureState.MEASURED,
        TokenMeasureState.MEASURED_ZERO,
    ):
        reason = "source_reason_unrecorded"
    return {
        "measure": measure.to_dict(),
        "invocation_id": raw_span.get("invocation_id"),
        "parent_session_key": parent_key,
        "child_key": child_key,
        "turn_id": raw_span.get("turn_id"),
        "source_id": raw_span.get("source_id"),
        "timestamp": raw_span.get("timestamp"),
        "timestamp_ms": span_time,
        "field": raw_span.get("field"),
        "reason": reason,
        "tokenizer_version": raw_span.get("tokenizer_version"),
        "encoding": raw_span.get("encoding"),
        "child_role": child.get("role"),
        "child_harness": child.get("harness"),
        "child_provider": child.get("provider"),
        "child_model": child.get("model"),
    }


def _add_untimed_span(
    groups: dict[_SpanGroupKey, dict[str, Any]],
    *,
    owner: Mapping[str, Any],
    raw_span: Mapping[str, Any],
    child: Mapping[str, Any],
    parent_key: str,
    child_key: str,
    measure: TokenMeasure,
) -> None:
    group = _group_for_span(
        groups,
        owner,
        raw_span.get("model"),
        harness=raw_span.get("harness"),
        provider=raw_span.get("provider"),
    )
    group["untimed_excluded"] += 1
    provenance = _span_provenance(measure, raw_span, child, parent_key, child_key, None)
    provenance["reason"] = "untimed_observation_excluded"
    group["provenance"].append(provenance)


def _add_selected_span(
    groups: dict[_SpanGroupKey, dict[str, Any]],
    *,
    owners_with_spans: set[str],
    eligible_keys: set[str],
    owner: Mapping[str, Any],
    raw_span: Mapping[str, Any],
    child: Mapping[str, Any],
    parent_key: str,
    child_key: str,
    measure: TokenMeasure,
    span_time: int | None,
) -> None:
    field = raw_span["field"]
    group = _group_for_span(
        groups,
        owner,
        raw_span.get("model"),
        harness=raw_span.get("harness"),
        provider=raw_span.get("provider"),
    )
    owners_with_spans.add(parent_key)
    if parent_key in eligible_keys:
        group["eligible_parent_keys"].add(parent_key)
    invocation_id = raw_span.get("invocation_id")
    group["eligible_invocations"].add((parent_key, child_key, invocation_id))
    provenance = _span_provenance(measure, raw_span, child, parent_key, child_key, span_time)
    group["spans"][field].append({**provenance, "measure": measure})
    group["provenance"].append(provenance)
    if isinstance(invocation_id, str) and invocation_id:
        invocation_key = (parent_key, child_key, invocation_id)
        invocation = group["invocations"].setdefault(
            invocation_key,
            {"parent_prompt_tokens": [], "subagent_return_tokens": []},
        )
        invocation[field].append(measure)


def _collect_candidate_span(
    groups: dict[_SpanGroupKey, dict[str, Any]],
    *,
    owners_with_spans: set[str],
    eligible_keys: set[str],
    owner: Mapping[str, Any],
    child: Mapping[str, Any],
    raw_span: object,
    parent_key: str,
    child_key: str,
    window: str,
    generated_at_ms: int,
) -> None:
    if not isinstance(raw_span, Mapping):
        return
    field = raw_span.get("field")
    if field not in ("parent_prompt_tokens", "subagent_return_tokens"):
        return
    measure = _decode_measure(raw_span, "measure")
    span_time = _timestamp_ms(raw_span.get("timestamp"))
    if span_time is not None and not _time_in_window(
        span_time, window, generated_at_ms=generated_at_ms
    ):
        return
    if span_time is None and measure.state in (
        TokenMeasureState.MEASURED,
        TokenMeasureState.MEASURED_ZERO,
    ):
        _add_untimed_span(
            groups,
            owner=owner,
            raw_span=raw_span,
            child=child,
            parent_key=parent_key,
            child_key=child_key,
            measure=measure,
        )
        return
    if span_time is None and parent_key not in eligible_keys:
        return
    _add_selected_span(
        groups,
        owners_with_spans=owners_with_spans,
        eligible_keys=eligible_keys,
        owner=owner,
        raw_span=raw_span,
        child=child,
        parent_key=parent_key,
        child_key=child_key,
        measure=measure,
        span_time=span_time,
    )


def _child_key(child: Mapping[str, Any]) -> str:
    value = child.get("key")
    if isinstance(value, str):
        return value
    child_id = child.get("child_id")
    return child_id if isinstance(child_id, str) else "child"


def _collect_child_spans(
    groups: dict[_SpanGroupKey, dict[str, Any]],
    *,
    owners_with_spans: set[str],
    eligible_keys: set[str],
    owner: Mapping[str, Any],
    child: Mapping[str, Any],
    parent_key: str,
    window: str,
    generated_at_ms: int,
) -> None:
    child_key = _child_key(child)
    raw_spans = child.get("parent_context_spans")
    spans = raw_spans if isinstance(raw_spans, (list, tuple)) else ()
    if not spans:
        if parent_key in eligible_keys:
            group = _group_for_span(groups, owner, None)
            group["eligible_parent_keys"].add(parent_key)
            group["eligible_invocations"].add((parent_key, child_key, None))
        return
    for raw_span in spans:
        _collect_candidate_span(
            groups,
            owners_with_spans=owners_with_spans,
            eligible_keys=eligible_keys,
            owner=owner,
            child=child,
            raw_span=raw_span,
            parent_key=parent_key,
            child_key=child_key,
            window=window,
            generated_at_ms=generated_at_ms,
        )


def _collect_parent_groups(
    eligible_sessions: Sequence[Mapping[str, Any]],
    owners: Mapping[str, Mapping[str, Any]],
    children: Sequence[Mapping[str, Any]],
    *,
    window: str,
    generated_at_ms: int,
) -> dict[_SpanGroupKey, dict[str, Any]]:
    eligible_keys = {key for row in eligible_sessions if isinstance((key := row.get("key")), str)}
    groups: dict[_SpanGroupKey, dict[str, Any]] = {}
    owners_with_spans: set[str] = set()
    for child in children:
        parent_key = child.get("parent_session_key")
        if not isinstance(parent_key, str):
            continue
        owner = owners.get(parent_key)
        if owner is None:
            continue
        _collect_child_spans(
            groups,
            owners_with_spans=owners_with_spans,
            eligible_keys=eligible_keys,
            owner=owner,
            child=child,
            parent_key=parent_key,
            window=window,
            generated_at_ms=generated_at_ms,
        )
    for owner in eligible_sessions:
        owner_key = owner.get("key")
        if isinstance(owner_key, str) and owner_key not in owners_with_spans:
            _group_for_span(groups, owner, None)["eligible_parent_keys"].add(owner_key)
    return groups


def _field_span_summary(
    group: Mapping[str, Any],
    field: str,
    *,
    eligible_invocations: int,
) -> dict[str, Any]:
    records = [
        MeasureRecord(group["pair"], {field: span["measure"]}) for span in group["spans"][field]
    ]
    observed = [
        record
        for record in records
        if record.measures[field].state
        in (TokenMeasureState.MEASURED, TokenMeasureState.MEASURED_ZERO)
    ]
    summary = _aggregate_fields(records, (field,), eligible_count=eligible_invocations)[field]
    summary["observed_subset"] = _aggregate_fields(
        observed, (field,), eligible_count=eligible_invocations
    )[field]
    summary["reason_counts"] = dict(
        Counter(
            span["reason"]
            for span in group["provenance"]
            if span.get("field") == field and isinstance(span.get("reason"), str)
        )
    )
    return summary


def _invocation_measure(
    pair: SourcePair, values: Sequence[TokenMeasure], field: str
) -> TokenMeasure | None:
    if not values:
        return None
    summary = _aggregate_fields(
        [MeasureRecord(pair, {field: value}) for value in values], (field,)
    )[field]
    return TokenMeasure.measure_from_raw({"state": summary["state"], "value": summary["value"]})


def _matched_invocation(
    pair: SourcePair, invocation: Mapping[str, Sequence[TokenMeasure]]
) -> MeasureRecord | None:
    prompt = _invocation_measure(pair, invocation["parent_prompt_tokens"], "parent_prompt_tokens")
    returned = _invocation_measure(
        pair, invocation["subagent_return_tokens"], "subagent_return_tokens"
    )
    if prompt is None or returned is None:
        return None
    if prompt.state not in (TokenMeasureState.MEASURED, TokenMeasureState.MEASURED_ZERO):
        return None
    if returned.state not in (TokenMeasureState.MEASURED, TokenMeasureState.MEASURED_ZERO):
        return None
    return MeasureRecord(
        pair,
        {
            "parent_prompt_tokens": prompt,
            "subagent_return_tokens": returned,
        },
    )


def _matched_invocations(group: Mapping[str, Any]) -> list[MeasureRecord]:
    pair: SourcePair = group["pair"]
    records = []
    for invocation in group["invocations"].values():
        record = _matched_invocation(pair, invocation)
        if record is not None:
            records.append(record)
    return records


def _parent_metric_row(key: _SpanGroupKey, group: Mapping[str, Any]) -> dict[str, Any]:
    harness, provider, model, skill, recipe, step = key
    eligible = len(group["eligible_invocations"])
    prompt = _field_span_summary(group, "parent_prompt_tokens", eligible_invocations=eligible)
    returned = _field_span_summary(group, "subagent_return_tokens", eligible_invocations=eligible)
    matched = _matched_invocations(group)
    comparison = _ratio_field(
        matched,
        "subagent_return_tokens",
        "parent_prompt_tokens",
        eligible_count=eligible,
    )
    return {
        "harness": harness,
        "provider": provider,
        "model": model,
        "skill": skill,
        "recipe": recipe,
        "step": step,
        "level": group["level"],
        "prompt": prompt,
        "returned": returned,
        "comparison": comparison,
        "eligible_invocations": eligible,
        "matched_invocations": len(matched),
        "eligible_parent_sessions": len(group["eligible_parent_keys"]),
        "untimed_excluded": group["untimed_excluded"],
        "provenance": group["provenance"],
        "source_basis": (
            "Tokenizer counts of explicit recorded prompt/message and returned text "
            "at the parent invocation model; argument metadata, hidden/system/framing "
            "tokens, and unrelated parent work are excluded."
        ),
    }


def _parent_context_metrics(
    eligible_sessions: Sequence[Mapping[str, Any]],
    all_sessions: Sequence[Mapping[str, Any]],
    children: Sequence[Mapping[str, Any]],
    *,
    levels: Sequence[str | None],
    window: str,
    generated_at_ms: int,
) -> list[dict[str, Any]]:
    owners: dict[str, Mapping[str, Any]] = {
        key: row
        for row in all_sessions
        if isinstance((key := row.get("key")), str) and row.get("level") in set(levels)
    }
    groups = _collect_parent_groups(
        eligible_sessions,
        owners,
        children,
        window=window,
        generated_at_ms=generated_at_ms,
    )
    ordered_keys = sorted(groups, key=lambda key: tuple(str(value or "") for value in key))
    return [_parent_metric_row(key, groups[key]) for key in ordered_keys]
