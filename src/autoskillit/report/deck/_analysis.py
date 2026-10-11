"""Frozen measurements and retained, interpreted instances of the observability deck."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from autoskillit.core import validate_rollout_marker

from ._measure_helpers import TOKEN_FIELDS
from ._payload import build_deck_payload
from ._trend import _trend_delta, _trend_group_key, _trend_measure_summary

_LIMITATION = "Coverage includes retained report-index sources, not complete historical coverage."
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _json_bytes(value: Any) -> bytes:
    """Compact sorted-key UTF-8 JSON, retaining finite measured floats."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _canonical_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    copied = [deepcopy(dict(row)) for row in rows]
    return sorted(copied, key=lambda row: (str(row.get("key", "")), _json_bytes(row)))


def _coverage(populations: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    result: dict[str, Any] = {"limitation": _LIMITATION, "populations": {}}
    for name, rows in populations.items():
        result["populations"][name] = {
            "rows": len(rows),
            "untimed_rows": sum(
                not isinstance(row.get("time_ms"), int) or isinstance(row.get("time_ms"), bool)
                for row in rows
            ),
            "measure_states": {
                field: dict(
                    sorted(
                        Counter(
                            row[field].get("state", "unknown")
                            if isinstance(row.get(field), Mapping)
                            else "unknown"
                            for row in rows
                        ).items()
                    )
                )
                for field in TOKEN_FIELDS
            },
        }
    return result


def _selected_runs(
    sessions: list[dict[str, Any]], change_id: str
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], datetime]]]:
    selected: list[tuple[dict[str, Any], datetime]] = []
    declaration: dict[str, Any] | None = None
    for row in sessions:
        raw = row.get("rollout")
        if raw is None:
            continue
        if not isinstance(raw, Mapping):
            raise ValueError("rollout evidence must be an object or null when cohorting")
        if raw.get("change_id") != change_id:
            continue
        marker = validate_rollout_marker(raw, field_name="rollout")
        if declaration is not None and marker != declaration:
            raise ValueError(f"conflicting rollout declarations for {change_id}")
        declaration = dict(marker)
        timestamp = row.get("timestamp")
        try:
            instant = datetime.fromisoformat(timestamp) if isinstance(timestamp, str) else None
        except ValueError as exc:
            raise ValueError("selected run timestamp must be an aware ISO-8601 timestamp") from exc
        if instant is None or instant.utcoffset() is None:
            raise ValueError("selected run timestamp must be an aware ISO-8601 timestamp")
        selected.append((row, instant))
    if declaration is None:
        raise ValueError(f"no retained runs match rollout change_id {change_id}")
    return declaration, selected


def _cohort(sessions: list[dict[str, Any]], change_id: str) -> dict[str, Any]:
    if not change_id:
        return {"change_id": None, "reason": "No rollout cohort applied.", "comparisons": []}
    declaration, selected = _selected_runs(sessions, change_id)
    cutover = datetime.fromisoformat(declaration["effective_at"])
    groups: dict[tuple[Any, ...], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {"before": [], "after": []}
    )
    before_count = 0
    for row, instant in selected:
        side = "before" if instant < cutover else "after"
        before_count += side == "before"
        groups[_trend_group_key(row)][side].append(row)
    comparisons: list[dict[str, Any]] = []
    for group, sides in sorted(groups.items(), key=lambda item: _json_bytes(item[0])):
        before, after = (_trend_measure_summary(sides[side]) for side in ("before", "after"))
        for metric in (*TOKEN_FIELDS, "failure_share"):
            comparisons.append(
                {
                    "group": list(group),
                    "metric": metric,
                    "before": before["failure_share"]
                    if metric == "failure_share"
                    else before["measures"][metric],
                    "after": after["failure_share"]
                    if metric == "failure_share"
                    else after["measures"][metric],
                    "delta": _trend_delta(
                        before,
                        after,
                        metric,
                        unit="percentage_points" if metric == "failure_share" else "tokens",
                    ),
                }
            )
    return {
        **declaration,
        "before_count": before_count,
        "after_count": len(selected) - before_count,
        "excluded_count": len(sessions) - len(selected),
        "marked_pre_cutover_observation": before_count > 0,
        "preregistration": (
            "Marked pre-cutover observations provide a baseline; logical start time does not "
            "independently prove when the prediction was registered."
            if before_count
            else "After-only declarations do not establish preregistration; "
            "no marked pre-cutover baseline was retained."
        ),
        "comparisons": comparisons,
    }


def _measured_nodes(value: Any, path: str) -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        if "value" in value and "state" in value:
            yield path, value
        else:
            for key in sorted(value):
                yield from _measured_nodes(value[key], path + "/" + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _measured_nodes(child, path + "/" + str(index))


def _evidence(payload: dict[str, Any], cohort: dict[str, Any]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    sources = {
        "cohort": "sessions",
        "spend": "skills",
        "efficiency": "skills",
        "skill": "skills",
        "role": "roles",
    }
    for view in payload["views"]:
        if view["status"] != "built":
            continue
        view_id = view["id"]
        table_name = sources.get(view_id, view_id)
        table = payload["tables"].get(table_name)
        if table:
            for index, row in enumerate(table["rows"]):
                measurement = dict(zip(table["columns"], row, strict=True))
                identity = f"{view_id}/table/{index}"
                evidence[identity] = {
                    "view_id": view_id,
                    "source": identity,
                    "measurement": measurement,
                }
        prepared_name = sources.get(view_id, view_id)
        for path, measurement in _measured_nodes(
            payload["prepared"].get(prepared_name), prepared_name
        ):
            identity = view_id + "/prepared/" + path
            evidence[identity] = {"view_id": view_id, "source": path, "measurement": measurement}
    for comparison in cohort["comparisons"]:
        identity = "trend/rollout/" + _digest([comparison["group"], comparison["metric"]])
        evidence[identity] = {"view_id": "trend", "source": "rollout", "measurement": comparison}
    return evidence


def prepare_run_analysis(
    session_rows: Iterable[Mapping[str, Any]],
    *,
    request_rows: Iterable[Mapping[str, Any]] = (),
    tool_rows: Iterable[Mapping[str, Any]] = (),
    subagent_rows: Iterable[Mapping[str, Any]] = (),
    turn_rows: Iterable[Mapping[str, Any]] = (),
    index_schema_version: int,
    change_id: str = "",
    render_bundle: dict[str, Any],
) -> dict[str, Any]:
    """Freeze one canonical all-population snapshot and its prepared measurements."""
    populations = {
        name: _canonical_rows(rows)
        for name, rows in (
            ("sessions", session_rows),
            ("requests", request_rows),
            ("tools", tool_rows),
            ("subagents", subagent_rows),
            ("turns", turn_rows),
        )
    }
    anchor_ms = max(
        (
            row["time_ms"]
            for rows in populations.values()
            for row in rows
            if isinstance(row.get("time_ms"), int) and not isinstance(row.get("time_ms"), bool)
        ),
        default=0,
    )
    payload = build_deck_payload(
        populations["sessions"],
        request_rows=populations["requests"],
        tool_rows=populations["tools"],
        subagent_rows=populations["subagents"],
        turn_rows=populations["turns"],
        generated_at=_EPOCH + timedelta(milliseconds=anchor_ms),
        index_schema_version=index_schema_version,
    )
    cohort = _cohort(populations["sessions"], change_id)
    packet: dict[str, Any] = {
        "populations": populations,
        "payload": payload,
        "coverage": _coverage(populations),
        "cohort": cohort,
        "evidence": _evidence(payload, cohort),
        "render_bundle": deepcopy(render_bundle),
    }
    packet["input_fingerprint"] = _digest(packet)
    return packet
