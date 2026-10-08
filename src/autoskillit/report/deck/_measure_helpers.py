from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from autoskillit.core import (
    MeasureRecord,
    SourcePair,
    TokenMeasure,
    TokenMeasureState,
    aggregate_measures,
    measure_ratio,
)

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def _source_pair(row: Mapping[str, Any]) -> SourcePair:
    harness, provider = row.get("harness"), row.get("provider")
    return SourcePair(
        harness if isinstance(harness, str) and harness else "unknown",
        provider if isinstance(provider, str) and provider else "unknown",
    )


def _state_counts_empty() -> dict[str, int]:
    return {state.value: 0 for state in TokenMeasureState}


def _aggregate_fields(
    records: Sequence[MeasureRecord],
    fields: Sequence[str],
    *,
    eligible_count: int | None = None,
) -> dict[str, dict[str, Any]]:
    eligible = len(records) if eligible_count is None else eligible_count
    if not records:
        return {
            field: {
                "state": "no_observations",
                "value": None,
                "state_counts": _state_counts_empty(),
                "eligible_count": eligible,
                "observation_count": 0,
            }
            for field in fields
        }
    result = aggregate_measures(records, fields)
    return {
        field: {
            "state": aggregate.value.state.value,
            "value": aggregate.value.value,
            "state_counts": {
                state.value: count for state, count in aggregate.state_counts.items()
            },
            "eligible_count": eligible,
            "observation_count": result.runs,
        }
        for field, aggregate in result.fields.items()
    }


def _ratio_field(
    records: Sequence[MeasureRecord],
    numerator: str,
    denominator: str,
    *,
    eligible_count: int | None = None,
) -> dict[str, Any]:
    eligible = len(records) if eligible_count is None else eligible_count
    if not records:
        return {
            "state": "no_observations",
            "value": None,
            "sample_size": 0,
            "excluded_runs": 0,
            "unknown_runs": 0,
            "eligible_count": eligible,
            "observation_count": 0,
        }
    result = measure_ratio(records, numerator, denominator)
    return {
        "state": result.state.value,
        "value": result.value,
        "sample_size": result.sample_size,
        "excluded_runs": result.excluded_runs,
        "unknown_runs": result.unknown_runs,
        "eligible_count": eligible,
        "observation_count": len(records),
    }


def _coverage_state(state_counts: Mapping[str, int], observation_count: int) -> str:
    if observation_count == 0:
        return "no_observations"
    active = [state for state, count in state_counts.items() if count]
    return active[0] if len(active) == 1 else "mixed"


def _decode_measure(row: Mapping[str, Any], field: str) -> TokenMeasure:
    return TokenMeasure.measure_from_raw(row.get(field))


def _measure_records(
    rows: Sequence[Mapping[str, Any]], *, tool_names: Sequence[str] = ()
) -> list[MeasureRecord]:
    records = []
    for row in rows:
        harness = row.get("harness")
        provider = row.get("provider")
        pair = SourcePair(
            harness if isinstance(harness, str) and harness else "unknown",
            provider if isinstance(provider, str) and provider else "unknown",
        )
        child = row.get("actor_level") == "L0"
        raw = row.get("token_usage") if child else row
        raw = raw if isinstance(raw, Mapping) else {}
        usage_known = not child or row.get("usage_state") == "observed"
        measures = {
            field: TokenMeasure.measure_from_raw(raw.get(field))
            if usage_known
            else TokenMeasure.unknown()
            for field in TOKEN_FIELDS
        }
        if child:
            counts = row.get("tool_counts")
            transcript_known = row.get("transcript_state") == "observed" and isinstance(
                counts, Mapping
            )
            counts = counts if isinstance(counts, Mapping) else {}
            measures["tool_calls"] = (
                TokenMeasure.observed(sum(counts.values()))
                if transcript_known
                else TokenMeasure.unknown()
            )
            for tool in tool_names:
                measures[f"tool:{tool}"] = (
                    TokenMeasure.observed(counts.get(tool, 0))
                    if transcript_known
                    else TokenMeasure.unknown()
                )
        records.append(MeasureRecord(pair, measures))
    return records


def _measure_payload(records: Sequence[MeasureRecord], fields: Sequence[str]) -> dict[str, Any]:
    result = aggregate_measures(records, fields)
    return {
        field: {
            "state": aggregate.value.state.value,
            "value": aggregate.value.value,
            "state_counts": {
                state.value: count for state, count in aggregate.state_counts.items()
            },
        }
        for field, aggregate in result.fields.items()
    }


def _ratio_payload(
    records: Sequence[MeasureRecord],
    numerator: str,
    denominator: str,
    *,
    sample_unit: str,
    definition_roles: Sequence[str],
    definitions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    result = measure_ratio(records, numerator, denominator)
    roles = sorted(set(definition_roles))
    missing = [role for role in roles if definitions.get(role, {}).get("state") != "available"]
    eligible = (
        result.state in (TokenMeasureState.MEASURED, TokenMeasureState.MEASURED_ZERO)
        and result.value is not None
        and result.sample_size > 0
        and bool(roles)
        and not missing
    )
    reasons = []
    if result.state is TokenMeasureState.UNKNOWN:
        reasons.append("the ratio has unknown contributing measurements")
    elif result.state is TokenMeasureState.UNAVAILABLE:
        reasons.append("the ratio is unavailable for this source")
    elif result.state is TokenMeasureState.NOT_APPLICABLE:
        reasons.append("the ratio has no applicable positive denominator")
    if not roles:
        reasons.append("no contributor role definitions are linked")
    if missing:
        reasons.append("missing definitions: " + ", ".join(missing))
    return {
        "state": result.state.value,
        "value": result.value,
        "numerator": result.numerator_field,
        "denominator": result.denominator_field,
        "numerator_total": result.numerator_total,
        "denominator_total": result.denominator_total,
        "sample_unit": sample_unit,
        "sample_size": result.sample_size,
        "excluded_runs": result.excluded_runs,
        "unknown_runs": result.unknown_runs,
        "definition_roles": roles,
        "review_eligible": eligible,
        "review_reason": None if eligible else "; ".join(reasons),
    }


def _metric_payload(
    rows: Sequence[Mapping[str, Any]],
    *,
    sample_unit: str,
    definition_roles: Sequence[str],
    definitions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    tool_names = sorted(
        {
            tool
            for row in rows
            if row.get("transcript_state") == "observed"
            and isinstance(row.get("tool_counts"), Mapping)
            for tool in row["tool_counts"]
            if isinstance(tool, str)
        }
    )
    records = _measure_records(rows, tool_names=tool_names)
    fields: tuple[str, ...] = TOKEN_FIELDS
    has_tool_metrics = sample_unit == "child-invocation"
    if has_tool_metrics:
        fields += ("tool_calls",) + tuple(f"tool:{tool}" for tool in tool_names)
    measures = _measure_payload(records, fields)
    ratios = {
        "input_output": _ratio_payload(
            records,
            "input_tokens",
            "output_tokens",
            sample_unit=sample_unit,
            definition_roles=definition_roles,
            definitions=definitions,
        ),
        "cache_share": _ratio_payload(
            records,
            "cache_read_tokens",
            "input_tokens",
            sample_unit=sample_unit,
            definition_roles=definition_roles,
            definitions=definitions,
        ),
    }
    if has_tool_metrics:
        ratios["tool_mix"] = {
            tool: _ratio_payload(
                records,
                f"tool:{tool}",
                "tool_calls",
                sample_unit=sample_unit,
                definition_roles=definition_roles,
                definitions=definitions,
            )
            for tool in tool_names
        }
    return {"measures": measures, "ratios": ratios}


def _outcome_record(row: Mapping[str, Any], field: str = "failure") -> MeasureRecord:
    success = row.get("success")
    outcome = (
        TokenMeasure.observed(0 if success else 1)
        if isinstance(success, bool)
        else TokenMeasure.unknown()
    )
    denominator = TokenMeasure.observed(1) if isinstance(success, bool) else TokenMeasure.unknown()
    return MeasureRecord(_source_pair(row), {field: outcome, "known_attempt": denominator})


def _outcome_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    records = [_outcome_record(row) for row in rows]
    if not records:
        return {
            "failures": _aggregate_fields([], ("failure",))["failure"],
            "failure_rate": _ratio_field([], "failure", "known_attempt"),
            "eligible_count": 0,
            "observed_count": 0,
            "unknown_count": 0,
        }
    all_counts = _aggregate_fields(records, ("failure",))["failure"]["state_counts"]
    known = [record for record in records if record.measures["known_attempt"].value == 1]
    known_failure = _aggregate_fields(known, ("failure",))["failure"]
    rate = _ratio_field(known, "failure", "known_attempt", eligible_count=len(rows))
    return {
        "failures": known_failure,
        "failure_rate": rate,
        "state_counts": all_counts,
        "eligible_count": len(rows),
        "observed_count": len(known),
        "unknown_count": all_counts.get(TokenMeasureState.UNKNOWN.value, 0),
    }


def _ratio_state_measure(state: TokenMeasureState) -> TokenMeasure:
    if state is TokenMeasureState.MEASURED:
        return TokenMeasure.observed(1)
    if state is TokenMeasureState.MEASURED_ZERO:
        return TokenMeasure.observed(0)
    if state is TokenMeasureState.UNAVAILABLE:
        return TokenMeasure.unavailable()
    if state is TokenMeasureState.NOT_APPLICABLE:
        return TokenMeasure.not_applicable()
    return TokenMeasure.unknown()
