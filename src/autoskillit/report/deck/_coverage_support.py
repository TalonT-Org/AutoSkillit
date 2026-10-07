from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from autoskillit.core import SourcePair

from ._measure_helpers import _coverage_state, _state_counts_empty

_COVERAGE_LABELS: Mapping[str, tuple[str, str]] = {
    "input_tokens": (
        "How many input tokens are recorded?",
        "A session usage record with an input token measure",
    ),
    "output_tokens": (
        "How many output tokens are recorded?",
        "A session usage record with an output token measure",
    ),
    "cache_read_tokens": (
        "How many cache-read tokens are recorded?",
        "A session usage record with a cache-read token measure",
    ),
    "cache_write_tokens": (
        "How many cache-write tokens are recorded?",
        "A session usage record with a cache-write token measure",
    ),
    "resolved_model": (
        "Which resolved model was recorded?",
        "A session row with a resolved model",
    ),
    "context_window_tokens": (
        "Is the per-turn model window known?",
        "A turn with a positive resolved model window",
    ),
    "context_fraction": (
        "Can cache-read occupancy be computed?",
        "A turn with cache-read tokens and a positive model window",
    ),
    "context_fraction_validation": (
        "Does the stored fraction match the recomputed ratio?",
        "A turn with both stored and recomputed fractions",
    ),
    "turn_series_coverage": (
        "Is turn-level usage available?",
        "A session turn-usage ledger state",
    ),
    "session_outcomes": (
        "Are session outcomes known?",
        "A selected session with an explicit success outcome",
    ),
    "tool_errors": (
        "Are tool failure outcomes observable?",
        "An indexed tool_result event with a known success value",
    ),
    "tool_skill_step_attribution": (
        "Can tool events be attributed to a skill and step?",
        "An indexed tool_result event with an exact owning session and skill/step",
    ),
    "parent_prompt_tokens": (
        "Are parent prompt token counts available?",
        "A verified parent invocation with a timestamped prompt span",
    ),
    "subagent_return_tokens": (
        "Are returned text token counts available?",
        "A verified parent invocation with a timestamped return span",
    ),
}


def _coverage_row(
    *,
    pair: SourcePair,
    model: str | None,
    level: str | None,
    skill: str | None,
    recipe: str | None,
    step: str | None,
    population: str,
    field: str,
    measure: Mapping[str, Any],
    eligible_count: int,
    observation_count: int,
    source: str,
    time_ms: int | None = None,
    reason: str | None = None,
    state_counts: Mapping[str, int] | None = None,
    session_key: str | None = None,
) -> dict[str, Any]:
    counts = dict(state_counts or measure.get("state_counts") or _state_counts_empty())
    state = _coverage_state(counts, observation_count)
    measure_state = measure.get("state")
    if measure_state == "no_observations":
        state = "no_observations"
    elif measure_state == "mixed":
        state = "mixed"
    question, prerequisite = _COVERAGE_LABELS.get(
        field, (field.replace("_", " ").capitalize(), "A corresponding indexed observation")
    )
    return {
        "key": (
            f"{pair.harness}:{pair.provider}:{model}:{skill}:{recipe}:{step}:"
            f"{population}:{field}:{session_key or 'cohort'}"
        ),
        "session_key": session_key,
        "time_ms": time_ms,
        "harness": pair.harness,
        "provider": pair.provider,
        "model": model,
        "level": level,
        "skill": skill,
        "recipe": recipe,
        "step": step,
        "population": population,
        "field": field,
        "state": state,
        "measure": {"state": measure.get("state"), "value": measure.get("value")},
        "state_counts": counts,
        "eligible_count": eligible_count,
        "observation_count": observation_count,
        "question": question,
        "prerequisite": prerequisite,
        "reason": reason,
        "source": source,
    }


def _categorical_measure(
    values: Sequence[object],
) -> tuple[dict[str, Any], dict[str, int], str | None]:
    known = [value for value in values if isinstance(value, str) and value]
    unknown = len(values) - len(known)
    counts = {"measured": len(known), "unknown": unknown}
    distinct = sorted(set(known))
    if not values:
        state = "no_observations"
    elif len(distinct) > 1 or (known and unknown):
        state = "mixed"
    elif known:
        state = "measured"
    else:
        state = "unknown"
    reason = ", ".join(distinct) if distinct else "no resolved value was recorded"
    return {"state": state, "value": distinct[0] if len(distinct) == 1 else None}, counts, reason


def _gap_rows(coverage_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in coverage_rows
        if row.get("state") not in ("measured", "measured_zero")
        or (
            row.get("field") == "context_fraction_validation"
            and isinstance(row.get("measure"), Mapping)
            and row["measure"].get("value", 0) not in (None, 0)
        )
    ]
