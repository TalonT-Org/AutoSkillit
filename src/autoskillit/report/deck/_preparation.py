from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from autoskillit.core import TokenMeasureState

from ._context import _context_turn_rows, _parent_context_metrics, _span_measure
from ._coverage import _coverage_rows
from ._coverage_support import _gap_rows
from ._errors import _error_population_metrics, _error_rows, _selected_tool_events
from ._registry import (
    COVERAGE_COLUMNS,
    ERROR_COLUMNS,
    ERROR_TABLE,
    GAP_TABLE,
    PARITY_TABLE,
    TREND_COLUMNS,
    TREND_TABLE,
    TURN_COLUMNS,
    TURN_TABLE,
    WINDOWS,
)
from ._trend import _trend_view
from ._view_common import (
    _level_value,
    _population_blocks,
    _prepared_view_block,
    _time_in_window,
    _timestamp_ms,
    _window_level_rows,
    encode_table,
)

_BlockKey = tuple[str, tuple[str | None, ...]]


def _owner_rows(sessions: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {key: row for row in sessions if isinstance((key := row.get("key")), str)}


def _context_session_keys(
    selected_sessions: Sequence[Mapping[str, Any]],
    selected_rows: Sequence[Mapping[str, Any]],
    parent_metrics: Sequence[Mapping[str, Any]],
) -> set[str]:
    keys: set[str] = set()
    for row in selected_sessions:
        key = row.get("key")
        if isinstance(key, str):
            keys.add(key)
    for row in selected_rows:
        key = row.get("session_key")
        if isinstance(key, str):
            keys.add(key)
    for group in parent_metrics:
        for span in group.get("provenance", ()):
            key = span.get("parent_session_key")
            if isinstance(key, str):
                keys.add(key)
    return keys


def _block_keys(
    sessions: Sequence[Mapping[str, Any]],
    context_rows: Sequence[Mapping[str, Any]],
    event_rows: Sequence[Mapping[str, Any]],
    span_rows: Sequence[Mapping[str, Any]],
    *,
    generated_at_ms: int,
) -> tuple[list[_BlockKey], list[str | None], list[str | None]]:
    session_blocks = _population_blocks(sessions, generated_at_ms=generated_at_ms)
    context_blocks = _population_blocks(context_rows, generated_at_ms=generated_at_ms)
    error_blocks = _population_blocks(
        [*sessions, *event_rows, *span_rows], generated_at_ms=generated_at_ms
    )
    keys: set[_BlockKey] = {
        (window, tuple(levels))
        for window, levels, _ in (*session_blocks, *context_blocks, *error_blocks)
    }
    order = {definition.key: index for index, definition in enumerate(WINDOWS)}
    ordered = sorted(
        keys,
        key=lambda item: (
            order[item[0]],
            tuple((level is None, level or "") for level in item[1]),
        ),
    )
    session_levels = sorted(
        {_level_value(row.get("level")) for row in sessions},
        key=lambda level: (level is None, level or ""),
    )
    view_levels = sorted(
        {
            _level_value(row.get("level"))
            for row in (*sessions, *context_rows, *event_rows, *span_rows)
        },
        key=lambda level: (level is None, level or ""),
    )
    return ordered, session_levels, view_levels


def _context_session_views(
    window: str,
    selected_sessions: Sequence[Mapping[str, Any]],
    selected_rows: Sequence[Mapping[str, Any]],
    parent_metrics: Sequence[Mapping[str, Any]],
    all_context_rows: Sequence[Mapping[str, Any]],
    sessions: Sequence[Mapping[str, Any]],
    children: Sequence[Mapping[str, Any]],
    owners: Mapping[str, Mapping[str, Any]],
    cache: dict[tuple[str, str], dict[str, Any]],
    *,
    generated_at_ms: int,
) -> dict[str, dict[str, Any]]:
    keys = _context_session_keys(selected_sessions, selected_rows, parent_metrics)
    prepared: dict[str, dict[str, Any]] = {}
    for key in sorted(keys):
        owner = owners.get(key)
        if owner is None:
            continue
        cache_key = (window, key)
        if cache_key not in cache:
            owner_level = [_level_value(owner.get("level"))]
            rows = _window_level_rows(
                [row for row in all_context_rows if row.get("session_key") == key],
                window=window,
                levels=owner_level,
                generated_at_ms=generated_at_ms,
            )
            eligible = (
                [owner]
                if _time_in_window(owner.get("time_ms"), window, generated_at_ms=generated_at_ms)
                else []
            )
            spans = [child for child in children if child.get("parent_session_key") == key]
            metrics = _parent_context_metrics(
                eligible,
                sessions,
                spans,
                levels=owner_level,
                window=window,
                generated_at_ms=generated_at_ms,
            )
            cache[cache_key] = {"rows": rows, "metrics": {"parent_context": metrics}}
        prepared[key] = cache[cache_key]
    return prepared


def _context_block(
    window: str,
    levels: list[str | None],
    sessions_for_window: Sequence[Mapping[str, Any]],
    context_rows: Sequence[Mapping[str, Any]],
    sessions: Sequence[Mapping[str, Any]],
    children: Sequence[Mapping[str, Any]],
    owners: Mapping[str, Mapping[str, Any]],
    cache: dict[tuple[str, str], dict[str, Any]],
    *,
    generated_at_ms: int,
) -> dict[str, Any]:
    selected = _window_level_rows(
        context_rows,
        window=window,
        levels=levels,
        generated_at_ms=generated_at_ms,
    )
    parent_metrics = _parent_context_metrics(
        sessions_for_window,
        sessions,
        children,
        levels=levels,
        window=window,
        generated_at_ms=generated_at_ms,
    )
    session_views = _context_session_views(
        window,
        sessions_for_window,
        selected,
        parent_metrics,
        context_rows,
        sessions,
        children,
        owners,
        cache,
        generated_at_ms=generated_at_ms,
    )
    return {
        "window": window,
        "levels": levels,
        "rows": selected,
        "metrics": {
            "parent_context": parent_metrics,
            "eligible_sessions": len(sessions_for_window),
        },
        "sessions": session_views,
    }


def _error_block(
    window: str,
    levels: list[str | None],
    selected_sessions: Sequence[Mapping[str, Any]],
    sessions: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    *,
    generated_at_ms: int,
) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    rows, events = _error_rows(
        selected_sessions,
        sessions,
        tools,
        levels=levels,
        window=window,
        generated_at_ms=generated_at_ms,
    )
    return (
        {
            "window": window,
            "levels": levels,
            "rows": rows,
            "metrics": {"populations": _error_population_metrics(selected_sessions, events)},
        },
        events,
    )


def _trend_block(
    window: str,
    levels: list[str | None],
    sessions: Sequence[Mapping[str, Any]],
    selected_sessions: Sequence[Mapping[str, Any]],
    *,
    generated_at_ms: int,
) -> dict[str, Any]:
    rows, comparisons, metadata = _trend_view(
        sessions,
        selected_sessions,
        levels=levels,
        window=window,
        generated_at_ms=generated_at_ms,
    )
    return {
        "window": window,
        "levels": levels,
        "rows": rows,
        "metrics": {"comparisons": comparisons, **metadata},
    }


def _coverage_session_views(
    window: str,
    session_keys: set[str],
    owners: Mapping[str, Mapping[str, Any]],
    sessions: Sequence[Mapping[str, Any]],
    turns: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    children: Sequence[Mapping[str, Any]],
    cache: dict[tuple[str, str], dict[str, Any]],
    *,
    generated_at_ms: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    gaps: dict[str, dict[str, Any]] = {}
    parity: dict[str, dict[str, Any]] = {}
    for key in sorted(session_keys):
        owner = owners.get(key)
        if owner is None:
            continue
        cache_key = (window, key)
        if cache_key not in cache:
            owner_level = [_level_value(owner.get("level"))]
            coverage = _coverage_rows(
                [owner],
                sessions,
                turns,
                tools,
                children,
                levels=owner_level,
                window=window,
                generated_at_ms=generated_at_ms,
                session_key=key,
            )
            missing = _gap_rows(coverage)
            cache[cache_key] = {
                "coverage": coverage,
                "gaps": missing,
                "metrics": {
                    "eligible_sessions": 1,
                    "gap_count": len(missing),
                    "coverage_count": len(coverage),
                },
            }
        entry = cache[cache_key]
        gaps[key] = {"rows": entry["gaps"], "metrics": entry["metrics"]}
        parity[key] = {"rows": entry["coverage"], "metrics": entry["metrics"]}
    return gaps, parity


def _coverage_block(
    window: str,
    levels: list[str | None],
    selected_sessions: Sequence[Mapping[str, Any]],
    selected_events: Sequence[Mapping[str, Any]],
    parent_context: Sequence[Mapping[str, Any]],
    sessions: Sequence[Mapping[str, Any]],
    turns: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    children: Sequence[Mapping[str, Any]],
    owners: Mapping[str, Mapping[str, Any]],
    cache: dict[tuple[str, str], dict[str, Any]],
    *,
    generated_at_ms: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    coverage = _coverage_rows(
        selected_sessions,
        sessions,
        turns,
        tools,
        children,
        levels=levels,
        window=window,
        generated_at_ms=generated_at_ms,
    )
    gaps = _gap_rows(coverage)
    observed_turns = _window_level_rows(
        turns,
        window=window,
        levels=levels,
        generated_at_ms=generated_at_ms,
    )
    session_keys: set[str] = set()
    for row in selected_sessions:
        key = row.get("key")
        if isinstance(key, str):
            session_keys.add(key)
    for row in observed_turns:
        key = row.get("session_key")
        if isinstance(key, str):
            session_keys.add(key)
    for event in selected_events:
        key = event.get("session_key")
        if event.get("attribution_state") == "attributed" and isinstance(key, str):
            session_keys.add(key)
    for group in parent_context:
        for span in group.get("provenance", ()):
            key = span.get("parent_session_key")
            if isinstance(key, str):
                session_keys.add(key)
    per_session_gaps, per_session_parity = _coverage_session_views(
        window,
        session_keys,
        owners,
        sessions,
        turns,
        tools,
        children,
        cache,
        generated_at_ms=generated_at_ms,
    )
    metrics = {
        "eligible_sessions": len(selected_sessions),
        "gap_count": len(gaps),
        "coverage_count": len(coverage),
    }
    return (
        {
            "window": window,
            "levels": levels,
            "rows": gaps,
            "metrics": metrics,
            "sessions": per_session_gaps,
        },
        {
            "window": window,
            "levels": levels,
            "rows": coverage,
            "metrics": metrics,
            "sessions": per_session_parity,
        },
    )


def _span_population_rows(
    sessions: Sequence[Mapping[str, Any]],
    children: Sequence[Mapping[str, Any]],
    *,
    generated_at_ms: int,
) -> list[Mapping[str, Any]]:
    owners: dict[str, Mapping[str, Any]] = {
        key: row for row in sessions if isinstance((key := row.get("key")), str)
    }
    rows: list[Mapping[str, Any]] = []
    for child in children:
        parent_key = child.get("parent_session_key")
        owner = owners.get(parent_key) if isinstance(parent_key, str) else None
        if owner is None:
            continue
        spans = child.get("parent_context_spans")
        if not isinstance(spans, (list, tuple)):
            continue
        for span in spans:
            if not isinstance(span, Mapping):
                continue
            time_ms = _timestamp_ms(span.get("timestamp"))
            if time_ms is not None and time_ms > generated_at_ms:
                continue
            measure = _span_measure(span)
            if time_ms is None and measure.state in (
                TokenMeasureState.MEASURED,
                TokenMeasureState.MEASURED_ZERO,
            ):
                continue
            owner_time = owner.get("time_ms")
            if time_ms is None and not _time_in_window(
                owner_time, "all", generated_at_ms=generated_at_ms
            ):
                continue
            rows.append(
                {
                    "key": span.get("source_id") or child.get("key"),
                    "session_key": parent_key,
                    "time_ms": time_ms if time_ms is not None else owner_time,
                    "harness": span.get("harness") or owner.get("harness"),
                    "provider": span.get("provider") or owner.get("provider"),
                    "model": span.get("model"),
                    "skill": owner.get("skill"),
                    "recipe": owner.get("recipe"),
                    "step": owner.get("step"),
                    "level": owner.get("level"),
                }
            )
    return rows


def _all_history_projections(
    sessions: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    context_rows: Sequence[Mapping[str, Any]],
    span_rows: Sequence[Mapping[str, Any]],
    blocks: Mapping[str, Sequence[Mapping[str, Any]]],
    session_levels: Sequence[str | None],
    view_levels: Sequence[str | None],
    *,
    generated_at_ms: int,
) -> tuple[dict[str, Any], dict[str, list[Mapping[str, Any]]]]:
    all_context = [
        row
        for row in context_rows
        if _time_in_window(row.get("time_ms"), "all", generated_at_ms=generated_at_ms)
    ]
    _, all_events = _error_rows(
        sessions,
        sessions,
        tools,
        levels=session_levels,
        window="all",
        generated_at_ms=generated_at_ms,
    )
    session_population = [
        row
        for row in sessions
        if _time_in_window(row.get("time_ms"), "all", generated_at_ms=generated_at_ms)
    ]
    error_population = [*session_population, *all_events]
    gap_population: list[Mapping[str, Any]] = [
        *session_population,
        *[row for row in all_context if row.get("kind") == "turn"],
        *all_events,
        *span_rows,
    ]
    view_rows: dict[str, list[Mapping[str, Any]]] = {
        "context": [*all_context, *span_rows],
        "errors": error_population,
        "trend": session_population,
        "gaps": gap_population,
        "parity": gap_population,
    }

    def table_rows(view: str) -> list[Mapping[str, Any]]:
        block = _prepared_view_block(blocks[view], window="all", levels=view_levels)
        return list(block.get("rows", ())) if block else []

    tables = {
        TURN_TABLE: encode_table(
            [row for row in all_context if row.get("kind") == "turn"],
            TURN_COLUMNS,
        ),
        ERROR_TABLE: encode_table(table_rows("errors"), ERROR_COLUMNS),
        TREND_TABLE: encode_table(table_rows("trend"), TREND_COLUMNS),
        GAP_TABLE: encode_table(table_rows("gaps"), COVERAGE_COLUMNS),
        PARITY_TABLE: encode_table(table_rows("parity"), COVERAGE_COLUMNS),
    }
    return tables, view_rows


def _prepare_additional_views(
    sessions: Sequence[Mapping[str, Any]],
    requests: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    children: Sequence[Mapping[str, Any]],
    turns: Sequence[Mapping[str, Any]],
    *,
    generated_at_ms: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[Mapping[str, Any]]]]:
    context_rows = _context_turn_rows(sessions, turns, requests, generated_at_ms=generated_at_ms)
    session_levels = sorted(
        {_level_value(row.get("level")) for row in sessions},
        key=lambda level: (level is None, level or ""),
    )
    event_rows = _selected_tool_events(
        tools,
        sessions,
        levels=session_levels,
        window="all",
        generated_at_ms=generated_at_ms,
    )
    span_rows = _span_population_rows(sessions, children, generated_at_ms=generated_at_ms)
    block_keys, session_levels, view_levels = _block_keys(
        sessions,
        context_rows,
        event_rows,
        span_rows,
        generated_at_ms=generated_at_ms,
    )
    owners = _owner_rows(sessions)
    context_cache: dict[tuple[str, str], dict[str, Any]] = {}
    coverage_cache: dict[tuple[str, str], dict[str, Any]] = {}
    context_blocks: list[dict[str, Any]] = []
    error_blocks: list[dict[str, Any]] = []
    trend_blocks: list[dict[str, Any]] = []
    gap_blocks: list[dict[str, Any]] = []
    parity_blocks: list[dict[str, Any]] = []
    for window, levels in block_keys:
        selected_sessions = _window_level_rows(
            sessions,
            window=window,
            levels=levels,
            generated_at_ms=generated_at_ms,
        )
        context_blocks.append(
            _context_block(
                window,
                list(levels),
                selected_sessions,
                context_rows,
                sessions,
                children,
                owners,
                context_cache,
                generated_at_ms=generated_at_ms,
            )
        )
        error_block, selected_events = _error_block(
            window,
            list(levels),
            selected_sessions,
            sessions,
            tools,
            generated_at_ms=generated_at_ms,
        )
        error_blocks.append(error_block)
        trend_blocks.append(
            _trend_block(
                window,
                list(levels),
                sessions,
                selected_sessions,
                generated_at_ms=generated_at_ms,
            )
        )
        gap_block, parity_block = _coverage_block(
            window,
            list(levels),
            selected_sessions,
            selected_events,
            context_blocks[-1]["metrics"]["parent_context"],
            sessions,
            turns,
            tools,
            children,
            owners,
            coverage_cache,
            generated_at_ms=generated_at_ms,
        )
        gap_blocks.append(gap_block)
        parity_blocks.append(parity_block)
    blocks: dict[str, Sequence[Mapping[str, Any]]] = {
        "errors": error_blocks,
        "trend": trend_blocks,
        "gaps": gap_blocks,
        "parity": parity_blocks,
    }
    additional_tables, view_rows = _all_history_projections(
        sessions,
        tools,
        context_rows,
        span_rows,
        blocks,
        session_levels,
        view_levels,
        generated_at_ms=generated_at_ms,
    )
    prepared = {
        "context": {"blocks": context_blocks},
        "errors": {"blocks": error_blocks},
        "trend": {"blocks": trend_blocks},
        "gaps": {"blocks": gap_blocks},
        "parity": {"blocks": parity_blocks},
    }
    return prepared, additional_tables, view_rows
