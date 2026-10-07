"""Build the static deck model from resolved report-index session rows."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from autoskillit.core import TokenMeasureState, load_bundled_agent_definitions

from ._metric_blocks import _role_blocks, _skill_blocks
from ._preparation import _prepare_additional_views
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
from ._view_common import DAY_MS, SKILL_LEVELS, _canonical_children, encode_table


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


def _prepare_deck_data(
    sessions: Sequence[Mapping[str, Any]],
    requests: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    subagents: Iterable[Mapping[str, Any]],
    turns: Sequence[Mapping[str, Any]],
    *,
    generated_at_ms: int,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    children = _canonical_children(sessions, subagents)
    additional_prepared, additional_tables, additional_view_rows = _prepare_additional_views(
        sessions,
        requests,
        tools,
        children,
        turns,
        generated_at_ms=generated_at_ms,
    )
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
        **additional_view_rows,
    }
    population_labels = {
        "spend": "skill",
        "efficiency": "contributor",
        "skill": "skill",
        "role": "child",
        "context": "turn",
        "errors": "error observation",
        "trend": "session",
        "gaps": "indexed evidence",
        "parity": "indexed evidence",
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
            **additional_prepared,
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
            **additional_tables,
        },
    )


def build_deck_payload(
    session_rows: Iterable[Mapping[str, Any]],
    *,
    request_rows: Iterable[Mapping[str, Any]] = (),
    tool_rows: Iterable[Mapping[str, Any]] = (),
    subagent_rows: Iterable[Mapping[str, Any]] = (),
    turn_rows: Iterable[Mapping[str, Any]] = (),
    generated_at: datetime,
    index_schema_version: int,
) -> dict[str, Any]:
    """Encode the report populations and prepare each built view's selected metrics."""
    if generated_at.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    generated_at_ms = (generated_at - epoch) // timedelta(milliseconds=1)
    rows = sorted(
        (dict(r) for r in session_rows),
        key=lambda r: (r.get("time_ms") is None, r.get("time_ms") or 0, r.get("key") or ""),
    )
    requests = [dict(row) for row in request_rows]
    tools = [dict(row) for row in tool_rows]
    subagents = [dict(row) for row in subagent_rows]
    turns = [dict(row) for row in turn_rows]
    history = _history(rows)
    prepared, identity_tables = _prepare_deck_data(
        rows,
        requests,
        tools,
        subagents,
        turns,
        generated_at_ms=generated_at_ms,
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
