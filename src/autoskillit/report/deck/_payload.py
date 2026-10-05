"""Build the static deck model from resolved report-index session rows."""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from autoskillit.core import TokenMeasureState

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
) -> list[dict[str, Any]]:
    chips = []
    for window in WINDOWS:
        reason = None
        count = 0
        if window.days is None:
            state, count = ChipState.LIVE, len(rows)
        elif history["first_ms"] is None:
            state = ChipState.ABSENT
            reason = ReasonDef("no session row in this index carries a timestamp")
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
                    reason = ReasonDef(f"no rows in the last {window.label}")
        chips.append(_chip(window.key, window.label, state, count, reason, days=window.days))
    return chips


def resolve_view_chips(
    view: DeckViewDef,
    rows: Sequence[Mapping[str, Any]],
    *,
    generated_at_ms: int,
    history: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    return {f.facet_id: _value_chips(view, f, rows) for f in FACETS} | {
        WINDOW_FACET_ID: _window_chips(rows, generated_at_ms=generated_at_ms, history=history)
    }


def build_deck_payload(
    session_rows: Iterable[Mapping[str, Any]], *, generated_at: datetime, index_schema_version: int
) -> dict[str, Any]:
    if generated_at.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    generated_at_ms = (generated_at - epoch) // timedelta(milliseconds=1)
    rows = sorted(
        (dict(r) for r in session_rows),
        key=lambda r: (r.get("time_ms") is None, r.get("time_ms") or 0, r.get("key") or ""),
    )
    history = _history(rows)
    return {
        "generated_at_ms": generated_at_ms,
        "index_schema_version": index_schema_version,
        "landing": LANDING_VIEW,
        "history": history,
        "tables": {SESSION_TABLE: encode_table(rows, SESSION_COLUMNS)},
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
        "chips": {
            v.view_id: resolve_view_chips(
                v, rows, generated_at_ms=generated_at_ms, history=history
            )
            for v in DECK_VIEWS
            if v.planned_issue is None
        },
        "availability": [
            {
                "state": s.value,
                "label": AVAILABILITY_VOCABULARY[s][0],
                "description": AVAILABILITY_VOCABULARY[s][1],
            }
            for s in TokenMeasureState
        ],
    }
