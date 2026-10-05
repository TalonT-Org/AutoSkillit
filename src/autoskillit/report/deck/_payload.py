"""Build the static deck model from resolved report-index session rows."""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from itertools import combinations
from typing import Any

from autoskillit.core import (
    MeasureRecord,
    SourcePair,
    TokenMeasure,
    TokenMeasureState,
    aggregate_measures,
    load_bundled_agent_definitions,
    measure_ratio,
)

from ._registry import (
    AVAILABILITY_VOCABULARY,
    DECK_VIEWS,
    FACETS,
    LANDING_VIEW,
    SESSION_COLUMNS,
    SESSION_TABLE,
    WINDOW_FACET_ID,
    WINDOW_HISTORY_ISSUE,
    WINDOWS,
    ChipState,
    DeckViewDef,
    FacetDef,
    ReasonDef,
)

DAY_MS = 86_400_000
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
SKILL_LEVELS = ("skill", "orchestrator", None)


def encode_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> dict[str, Any]:
    return {"columns": list(columns), "rows": [[row.get(c) for c in columns] for row in rows]}


def _history(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    timed = [r["time_ms"] for r in rows if r.get("time_ms") is not None]
    return {
        "first_ms": min(timed) if timed else None,
        "last_ms": max(timed) if timed else None,
        "untimed": len(rows) - len(timed),
    }


def _chip(
    key: str, label: str, state: ChipState, count: int, reason: ReasonDef | None, **extra: Any
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "state": state.value,
        "count": count,
        "reason": reason.text if reason else None,
        "issue": reason.issue if reason else None,
        **extra,
    }


def _value_chips(
    view: DeckViewDef, facet: FacetDef, rows: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    counts = Counter(row.get(facet.column) for row in rows)
    recorded = any(v is not None for v in counts)
    chips = []
    for value in facet.declared:
        reason = dict(value.unresolvable_in).get(view.table) if view.table is not None else None
        count = counts.get(value.match, 0) if value.match is not None else 0
        if reason:
            state, count = ChipState.STRUCK, 0
        elif count:
            state = ChipState.LIVE
        else:
            state = ChipState.ABSENT
            reason = (
                facet.unrecorded_gap
                if not recorded and facet.unrecorded_gap
                else ReasonDef(f"no {value.label} rows in this index")
            )
        chips.append(_chip(value.key, value.label, state, count, reason, match=value.match))
    # L0's unresolvable None match must not consume the observed null bucket.
    declared_matches = {v.match for v in facet.declared if v.match is not None}
    observed_chips = []
    for observed, count in counts.items():
        if observed in declared_matches:
            continue
        if observed is None:
            if facet.null_label is None:
                raise ValueError(f"facet {facet.facet_id} has no null label")
            key = facet.null_label
        else:
            key = observed
        observed_chips.append(
            _chip(
                key,
                key,
                ChipState.LIVE,
                count,
                None,
                match=observed,
            )
        )
    return chips + sorted(observed_chips, key=lambda c: (-c["count"], c["key"]))


def _window_chips(
    rows: Sequence[Mapping[str, Any]],
    *,
    generated_at_ms: int,
    history: Mapping[str, Any],
    population_label: str | None = None,
) -> list[dict[str, Any]]:
    chips = []
    for window in WINDOWS:
        reason = None
        count = 0
        if window.days is None:
            state, count = ChipState.LIVE, len(rows)
        elif history["first_ms"] is None:
            state = ChipState.ABSENT
            reason = ReasonDef(
                f"no {population_label} row in this view carries a timestamp"
                if population_label
                else "no session row in this index carries a timestamp"
            )
        else:
            first = history["first_ms"]
            retained = (generated_at_ms - first) // DAY_MS
            if retained < window.days:
                state = ChipState.ABSENT
                date = datetime.fromtimestamp(first / 1000, UTC).date().isoformat()
                reason = ReasonDef(
                    f"index history begins {date} — {retained} days retained", WINDOW_HISTORY_ISSUE
                )
            else:
                count = sum(
                    1
                    for r in rows
                    if r.get("time_ms") is not None
                    and r["time_ms"] >= generated_at_ms - window.days * DAY_MS
                )
                state = ChipState.LIVE if count else ChipState.ABSENT
                if not count:
                    reason = ReasonDef(
                        f"no {population_label} rows in the last {window.label}"
                        if population_label
                        else f"no rows in the last {window.label}"
                    )
        chips.append(_chip(window.key, window.label, state, count, reason, days=window.days))
    return chips


def resolve_view_chips(
    view: DeckViewDef,
    rows: Sequence[Mapping[str, Any]],
    *,
    generated_at_ms: int,
    history: Mapping[str, Any],
    population_label: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    return {f.facet_id: _value_chips(view, f, rows) for f in FACETS} | {
        WINDOW_FACET_ID: _window_chips(
            rows,
            generated_at_ms=generated_at_ms,
            history=history,
            population_label=population_label,
        )
    }


def _canonical_children(
    sessions: Sequence[Mapping[str, Any]],
    subagents: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    parents = {row.get("key"): row for row in sessions if isinstance(row.get("key"), str)}
    children = []
    for raw in subagents:
        row = dict(raw)
        parent_key = row.get("parent_session_key")
        parent = parents.get(parent_key)
        role = row.get("role")
        if (
            row.get("actor_level") != "L0"
            or not isinstance(row.get("child_id"), str)
            or not row["child_id"]
            or not isinstance(role, str)
            or not role
            or parent is None
            or not isinstance(row.get("native_parent_session_id"), str)
            or row["native_parent_session_id"] != parent.get("session_id")
        ):
            continue
        children.append(
            {
                **row,
                "harness": parent.get("harness") or "unknown",
                "provider": row.get("provider") or "unknown",
                "time_ms": parent.get("time_ms"),
                "level": parent.get("level"),
                "recipe": parent.get("recipe"),
                "step": parent.get("step"),
                "skill": row.get("skill") if isinstance(row.get("skill"), str) else None,
                "_parent_provider": parent.get("provider") or "unknown",
            }
        )
    return children


def _window_level_blocks(
    rows: Sequence[Mapping[str, Any]], *, generated_at_ms: int
) -> list[tuple[str, list[str | None], list[Mapping[str, Any]]]]:
    blocks: list[tuple[str, list[str | None], list[Mapping[str, Any]]]] = []
    for window in WINDOWS:
        in_window = [
            row
            for row in rows
            if window.days is None
            or (
                row.get("time_ms") is not None
                and row["time_ms"] >= generated_at_ms - window.days * DAY_MS
            )
        ]
        levels = sorted(
            {row.get("level") for row in in_window},
            key=lambda level: (level is None, level or ""),
        )
        for size in range(1, len(levels) + 1):
            for selected in combinations(levels, size):
                selected_set = set(selected)
                members = [row for row in in_window if row.get("level") in selected_set]
                if members:
                    blocks.append((window.key, list(selected), members))
    return blocks


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


def _skill_blocks(
    sessions: Sequence[Mapping[str, Any]],
    children: Sequence[Mapping[str, Any]],
    *,
    generated_at_ms: int,
    definitions: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    skill_rows: list[Mapping[str, Any]] = [
        row
        for row in sessions
        if row.get("level") in SKILL_LEVELS and isinstance(row.get("skill"), str)
    ]
    child_blocks = {
        (window, tuple(levels)): rows
        for window, levels, rows in _window_level_blocks(children, generated_at_ms=generated_at_ms)
    }
    blocks = []
    for window, levels, selected in _window_level_blocks(
        skill_rows, generated_at_ms=generated_at_ms
    ):
        groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
        for row in selected:
            identity = (
                row["skill"],
                row.get("harness") or "unknown",
                row.get("provider") or "unknown",
            )
            groups.setdefault(identity, []).append(row)
        rows_out = []
        selected_children = child_blocks.get((window, tuple(levels)), [])
        for skill, harness, provider in sorted(groups):
            runs = groups[(skill, harness, provider)]
            related = [
                row
                for row in selected_children
                if row.get("skill") == skill
                and row.get("harness") == harness
                and row.get("_parent_provider") == provider
            ]
            related_roles = sorted({row["role"] for row in related})
            metric = _metric_payload(
                runs,
                sample_unit="skill-run",
                definition_roles=related_roles,
                definitions=definitions,
            )
            step_groups: dict[tuple[str | None, str | None], list[Mapping[str, Any]]] = {}
            for run in runs:
                recipe = run.get("recipe") if isinstance(run.get("recipe"), str) else None
                step = run.get("step") if isinstance(run.get("step"), str) else None
                step_groups.setdefault((recipe, step), []).append(run)
            recipe_steps = []
            for recipe, step in sorted(
                step_groups,
                key=lambda value: (
                    value[0] is None,
                    value[0] or "",
                    value[1] is None,
                    value[1] or "",
                ),
            ):
                step_runs = step_groups[(recipe, step)]
                step_children = [
                    row
                    for row in related
                    if row.get("recipe") == recipe and row.get("step") == step
                ]
                step_metric = _metric_payload(
                    step_runs,
                    sample_unit="skill-run",
                    definition_roles=sorted({row["role"] for row in step_children}),
                    definitions=definitions,
                )
                recipe_steps.append(
                    {
                        "recipe": recipe,
                        "step": step,
                        **step_metric,
                        "exact_retransmission": TokenMeasure.unavailable().to_dict(),
                    }
                )
            role_groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
            for child in related:
                key = (child["role"], child["provider"], child["harness"])
                role_groups.setdefault(key, []).append(child)
            child_roles = []
            for role, role_provider, role_harness in sorted(role_groups):
                role_runs = role_groups[(role, role_provider, role_harness)]
                role_metric = _metric_payload(
                    role_runs,
                    sample_unit="child-invocation",
                    definition_roles=[role],
                    definitions=definitions,
                )
                child_roles.append(
                    {
                        "role": role,
                        "provider": role_provider,
                        "harness": role_harness,
                        "models": sorted(
                            {
                                row["model"]
                                for row in role_runs
                                if isinstance(row.get("model"), str)
                            }
                        ),
                        **role_metric,
                    }
                )
            rows_out.append(
                {
                    "skill": skill,
                    "harness": harness,
                    "provider": provider,
                    **metric,
                    "exact_retransmission": TokenMeasure.unavailable().to_dict(),
                    "recipe_steps": recipe_steps,
                    "child_roles": child_roles,
                }
            )
        blocks.append({"window": window, "levels": levels, "rows": rows_out})
    return blocks


def _role_blocks(
    children: Sequence[Mapping[str, Any]],
    *,
    generated_at_ms: int,
    definitions: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    role_rows: list[Mapping[str, Any]] = [
        row for row in children if row.get("level") in SKILL_LEVELS
    ]
    blocks = []
    for window, levels, selected in _window_level_blocks(
        role_rows, generated_at_ms=generated_at_ms
    ):
        groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for row in selected:
            groups.setdefault((row["role"], row["provider"]), []).append(row)
        rows_out = []
        for role, provider in sorted(groups):
            harness_groups: dict[str, list[Mapping[str, Any]]] = {}
            for row in groups[(role, provider)]:
                harness_groups.setdefault(row["harness"], []).append(row)
            harnesses = []
            for harness in sorted(harness_groups):
                invocations = harness_groups[harness]
                metric = _metric_payload(
                    invocations,
                    sample_unit="child-invocation",
                    definition_roles=[role],
                    definitions=definitions,
                )
                harnesses.append(
                    {
                        "harness": harness,
                        "models": sorted(
                            {
                                row["model"]
                                for row in invocations
                                if isinstance(row.get("model"), str)
                            }
                        ),
                        **metric,
                    }
                )
            rows_out.append({"role": role, "provider": provider, "harnesses": harnesses})
        blocks.append({"window": window, "levels": levels, "rows": rows_out})
    return blocks


def _prepare_deck_data(
    sessions: Sequence[Mapping[str, Any]],
    subagents: Iterable[Mapping[str, Any]],
    *,
    generated_at_ms: int,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    children = _canonical_children(sessions, subagents)
    skill_rows: list[Mapping[str, Any]] = [
        row
        for row in sessions
        if row.get("level") in SKILL_LEVELS and isinstance(row.get("skill"), str)
    ]
    role_rows: list[Mapping[str, Any]] = [
        row for row in children if row.get("level") in SKILL_LEVELS
    ]
    bundled = {definition.name: definition for definition in load_bundled_agent_definitions()}
    roles = sorted({row["role"] for row in role_rows})
    definitions: dict[str, dict[str, Any]] = {}
    for role in roles:
        definition = bundled.get(role)
        definitions[role] = (
            {
                "state": "available",
                "description": definition.description,
                "body": definition.body,
                "tools": list(definition.tools),
                "model": definition.model,
                "reader_tools": list(definition.reader_tools),
                "codex_model": getattr(getattr(definition, "codex", None), "model", None),
            }
            if definition is not None
            else {
                "state": "unavailable",
                "description": None,
                "body": None,
                "tools": [],
                "model": None,
                "reader_tools": [],
                "codex_model": None,
            }
        )
    skill_identities = sorted(
        {
            (
                row["skill"],
                row.get("harness") or "unknown",
                row.get("provider") or "unknown",
            )
            for row in skill_rows
        }
    )
    role_identities = sorted({(row["role"], row["provider"]) for row in role_rows})
    relationships = [
        {"skill": skill, "role": role, "harness": harness, "provider": provider}
        for skill, role, harness, provider in sorted(
            {
                (row["skill"], row["role"], row["harness"], row["provider"])
                for row in role_rows
                if isinstance(row.get("skill"), str) and row["skill"]
            }
        )
    ]
    view_rows: dict[str, list[Mapping[str, Any]]] = {
        "cohort": list(sessions),
        "spend": skill_rows,
        "efficiency": [*skill_rows, *role_rows],
        "skill": skill_rows,
        "role": role_rows,
    }
    population_labels = {
        "spend": "skill",
        "efficiency": "contributor",
        "skill": "skill",
        "role": "child",
    }
    view_chips: dict[str, dict[str, list[dict[str, Any]]]] = {}
    view_histories: dict[str, dict[str, Any]] = {}
    for view in DECK_VIEWS:
        if view.planned_issue is not None:
            continue
        rows = view_rows.get(view.view_id, list(sessions))
        history = _history(rows)
        view_chips[view.view_id] = resolve_view_chips(
            view,
            rows,
            generated_at_ms=generated_at_ms,
            history=history,
            population_label=population_labels.get(view.view_id),
        )
        view_histories[view.view_id] = history
    return (
        {
            "skills": _skill_blocks(
                skill_rows,
                role_rows,
                generated_at_ms=generated_at_ms,
                definitions=definitions,
            ),
            "roles": _role_blocks(
                role_rows, generated_at_ms=generated_at_ms, definitions=definitions
            ),
            "relationships": relationships,
            "definitions": definitions,
            "view_chips": view_chips,
            "view_histories": view_histories,
        },
        {
            "skills": encode_table(
                [
                    {"skill": skill, "harness": harness, "provider": provider}
                    for skill, harness, provider in skill_identities
                ],
                ("skill", "harness", "provider"),
            ),
            "roles": encode_table(
                [{"role": role, "provider": provider} for role, provider in role_identities],
                ("role", "provider"),
            ),
        },
    )


def build_deck_payload(
    session_rows: Iterable[Mapping[str, Any]],
    *,
    request_rows: Iterable[Mapping[str, Any]] = (),
    tool_rows: Iterable[Mapping[str, Any]] = (),
    subagent_rows: Iterable[Mapping[str, Any]] = (),
    generated_at: datetime,
    index_schema_version: int,
) -> dict[str, Any]:
    """Encode the session cohort and prepare attributed skill and child-role metrics.

    Request and tool rows remain available at this boundary, but contribute to no metric
    without a verified child identity. Native child rows already carry folded usage.
    """
    if generated_at.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    generated_at_ms = (generated_at - epoch) // timedelta(milliseconds=1)
    rows = sorted(
        (dict(r) for r in session_rows),
        key=lambda r: (r.get("time_ms") is None, r.get("time_ms") or 0, r.get("key") or ""),
    )
    history = _history(rows)
    prepared, identity_tables = _prepare_deck_data(
        rows, subagent_rows, generated_at_ms=generated_at_ms
    )
    tables = {SESSION_TABLE: encode_table(rows, SESSION_COLUMNS), **identity_tables}
    return {
        "generated_at_ms": generated_at_ms,
        "index_schema_version": index_schema_version,
        "landing": LANDING_VIEW,
        "history": history,
        "tables": tables,
        "facets": [
            {"id": f.facet_id, "label": f.label, "column": f.column, "kind": "values"}
            for f in FACETS
        ]
        + [{"id": WINDOW_FACET_ID, "label": "window", "column": "time_ms", "kind": "window"}],
        "views": [
            {
                "id": v.view_id,
                "question": v.question,
                "decision": v.decision,
                "group": v.group,
                "status": "built" if v.planned_issue is None else "planned",
                "issue": v.planned_issue,
            }
            for v in DECK_VIEWS
        ],
        "chips": prepared["view_chips"],
        "prepared": prepared,
        "availability": [
            {
                "state": s.value,
                "label": AVAILABILITY_VOCABULARY[s][0],
                "description": AVAILABILITY_VOCABULARY[s][1],
            }
            for s in TokenMeasureState
        ],
    }
