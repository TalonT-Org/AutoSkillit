from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from itertools import combinations
from typing import Any

from ._measure_helpers import _source_pair
from ._registry import WINDOWS

DAY_MS = 86_400_000
SKILL_LEVELS = ("skill", "orchestrator", None)


def encode_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> dict[str, Any]:
    return {"columns": list(columns), "rows": [[row.get(c) for c in columns] for row in rows]}


def _level_value(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _canonical_children(
    sessions: Sequence[Mapping[str, Any]],
    subagents: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    parents = {row.get("key"): row for row in sessions if isinstance(row.get("key"), str)}
    children = []
    for raw in subagents:
        row = dict(raw)
        parent_key = row.get("parent_session_key")
        parent = parents.get(parent_key) if isinstance(parent_key, str) else None
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
            {_level_value(row.get("level")) for row in in_window},
            key=lambda level: (level is None, level or ""),
        )
        for size in range(1, len(levels) + 1):
            for selected in combinations(levels, size):
                selected_set = set(selected)
                members = [row for row in in_window if row.get("level") in selected_set]
                if members:
                    blocks.append((window.key, list(selected), members))
    return blocks


def _time_in_window(time_ms: object, window: str, *, generated_at_ms: int) -> bool:
    if window == "all":
        return time_ms is None or (
            isinstance(time_ms, int)
            and not isinstance(time_ms, bool)
            and time_ms <= generated_at_ms
        )
    days = next((item.days for item in WINDOWS if item.key == window), None)
    if days is None or isinstance(time_ms, bool) or not isinstance(time_ms, int):
        return False
    return generated_at_ms - days * DAY_MS <= time_ms <= generated_at_ms


def _population_blocks(
    rows: Sequence[Mapping[str, Any]], *, generated_at_ms: int
) -> list[tuple[str, list[str | None], list[Mapping[str, Any]]]]:
    blocks: list[tuple[str, list[str | None], list[Mapping[str, Any]]]] = []
    for window in WINDOWS:
        in_window = [
            row
            for row in rows
            if _time_in_window(row.get("time_ms"), window.key, generated_at_ms=generated_at_ms)
        ]
        levels = sorted(
            {_level_value(row.get("level")) for row in in_window},
            key=lambda level: (level is None, level or ""),
        )
        for size in range(1, len(levels) + 1):
            for selected in combinations(levels, size):
                selected_set = set(selected)
                members = [row for row in in_window if row.get("level") in selected_set]
                if members:
                    blocks.append((window.key, list(selected), members))
    return blocks


def _timestamp_ms(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return int(parsed.timestamp() * 1000)


def _session_identity(
    row: Mapping[str, Any],
) -> tuple[str, str, str | None, str | None, str | None]:
    pair = _source_pair(row)
    return (
        pair.harness,
        pair.provider,
        row.get("skill") if isinstance(row.get("skill"), str) else None,
        row.get("recipe") if isinstance(row.get("recipe"), str) else None,
        row.get("step") if isinstance(row.get("step"), str) else None,
    )


def _window_level_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    window: str,
    levels: Sequence[str | None],
    generated_at_ms: int,
) -> list[Mapping[str, Any]]:
    level_set = set(levels)
    return [
        row
        for row in rows
        if row.get("level") in level_set
        and _time_in_window(row.get("time_ms"), window, generated_at_ms=generated_at_ms)
    ]


def _prepared_view_block(
    blocks: Sequence[Mapping[str, Any]], *, window: str, levels: Sequence[str | None]
) -> Mapping[str, Any] | None:
    wanted = tuple(levels)
    return next(
        (
            block
            for block in blocks
            if block.get("window") == window and tuple(block.get("levels", ())) == wanted
        ),
        None,
    )
