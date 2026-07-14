"""Ordering-violation detection over pipeline session step records.

Underscore-prefixed helper module (not ``tools_*.py``) — mechanical scan of
``sessions.jsonl``-style records; no ``recipe`` import needed. See
``tools/AGENTS.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

REVIEW_STEP_NAME = "review_approach"
PLAN_PRODUCING_STEP_NAMES = frozenset({"rectify", "make_plan"})


def read_session_index_records(log_root: Path) -> list[dict]:
    """Read sessions.jsonl entries as plain dicts. Tolerates corrupt lines."""
    index_path = Path(log_root) / "sessions.jsonl"
    if not index_path.exists():
        return []
    try:
        text = index_path.read_text(encoding="utf-8")
    except OSError:
        return []
    records: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def detect_ordering_violations(session_steps: list[dict]) -> list[dict]:
    """Detect REVIEW_BEFORE_PLAN violations across pipeline session step records.

    Args:
        session_steps: sessions.jsonl-style records, each with step_name,
            timestamp, kitchen_id.

    Returns:
        List of violation dicts: kitchen_id, violation_type, step_name,
        expected_predecessor.
    """
    by_kitchen: dict[str, list[dict]] = {}
    for rec in session_steps:
        kitchen_id = rec.get("kitchen_id", "")
        step_name = rec.get("step_name", "")
        timestamp = rec.get("timestamp", "")
        if not kitchen_id or not step_name or not timestamp:
            continue
        by_kitchen.setdefault(kitchen_id, []).append(rec)

    violations: list[dict] = []
    for kitchen_id, records in by_kitchen.items():
        records_sorted = sorted(records, key=lambda r: r.get("timestamp", ""))
        review_ts = next(
            (r["timestamp"] for r in records_sorted if r.get("step_name") == REVIEW_STEP_NAME),
            None,
        )
        if review_ts is None:
            continue
        plan_ts = next(
            (
                r["timestamp"]
                for r in records_sorted
                if r.get("step_name") in PLAN_PRODUCING_STEP_NAMES
            ),
            None,
        )
        if plan_ts is None or review_ts < plan_ts:
            violations.append(
                {
                    "kitchen_id": kitchen_id,
                    "violation_type": "REVIEW_BEFORE_PLAN",
                    "step_name": REVIEW_STEP_NAME,
                    "expected_predecessor": "rectify_or_make_plan",
                }
            )
    return violations
