#!/usr/bin/env python3
"""Capture and summarize Claude session lifecycle evidence for E2E runs."""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_SESSION_EVENTS = frozenset({"SessionStart", "SessionEnd"})
_AGENT_EVENTS = frozenset({"SubagentStart", "SubagentStop"})
_LIFECYCLE_EVENTS = _SESSION_EVENTS | _AGENT_EVENTS
_MAX_HOOK_INPUT_BYTES = 65_536
_MAX_TRACE_RECORD_BYTES = 8_192


def capture_hook_event(trace_path: Path, payload: Mapping[str, Any]) -> bool:
    """Append one allow-listed lifecycle record; return False for other hooks."""
    record = _hook_record(payload)
    if record is None:
        return False
    encoded = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > _MAX_TRACE_RECORD_BYTES:
        raise ValueError("lifecycle hook record exceeds the size limit")
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(trace_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        written = os.write(fd, encoded)
        if written != len(encoded):
            raise OSError("short lifecycle trace append")
    finally:
        os.close(fd)
    return True


def _hook_record(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    event = payload.get("hook_event_name", payload.get("event"))
    if event is None:
        return None
    if not isinstance(event, str):
        raise ValueError("lifecycle hook event must be a string")
    if event not in _LIFECYCLE_EVENTS:
        return None

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("lifecycle hook has no session_id")

    record: dict[str, Any] = {
        "event": event,
        "timestamp": time.time(),
        "session_id": session_id,
    }
    for key in (
        "agent_id",
        "agent_type",
        "source",
        "transcript_path",
        "agent_transcript_path",
    ):
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f"lifecycle hook {key} must be a string")
        record[key] = value
    if event in _AGENT_EVENTS and not record.get("agent_id"):
        raise ValueError("subagent lifecycle hook has no agent_id")
    return record


def _read_jsonl(
    path: Path, label: str, violations: list[str], *, collect: bool = True
) -> list[dict[str, Any]]:
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        violations.append(f"{label}:unavailable")
        return []

    records: list[dict[str, Any]] = []
    with handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                violations.append(f"{label}:empty_line:{line_number}")
                continue
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                violations.append(f"{label}:invalid_json:{line_number}")
                continue
            if not isinstance(item, dict):
                violations.append(f"{label}:invalid_record:{line_number}")
                continue
            if collect:
                records.append(item)
    return records


def _load_trace(trace_path: Path, violations: list[str]) -> list[dict[str, Any]]:
    records = _read_jsonl(trace_path, "trace", violations)
    valid: list[dict[str, Any]] = []
    for index, record in enumerate(records, 1):
        event = record.get("event")
        timestamp = record.get("timestamp")
        session_id = record.get("session_id")
        if not isinstance(event, str) or event not in _LIFECYCLE_EVENTS:
            violations.append(f"trace:unknown_event:{index}")
            continue
        finite_timestamp = False
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
            try:
                finite_timestamp = math.isfinite(timestamp)
            except OverflowError:
                pass
        if not finite_timestamp:
            violations.append(f"trace:invalid_timestamp:{index}")
            continue
        if not isinstance(session_id, str) or not session_id:
            violations.append(f"trace:invalid_session_id:{index}")
            continue
        optional_invalid = any(
            key in record and record[key] is not None and not isinstance(record[key], str)
            for key in (
                "agent_id",
                "agent_type",
                "source",
                "transcript_path",
                "agent_transcript_path",
            )
        )
        if optional_invalid:
            violations.append(f"trace:invalid_optional_field:{index}")
            continue
        if event in _AGENT_EVENTS and not record.get("agent_id"):
            violations.append(f"trace:missing_agent_id:{index}")
            continue
        valid.append(record)
    return valid


def _read_envelope(envelope: dict[str, Any] | None, violations: list[str]) -> tuple[str, str]:
    if not isinstance(envelope, dict):
        violations.append("envelope:missing")
        return "", ""
    session_id = envelope.get("dispatched_session_id")
    dispatch_id = envelope.get("dispatch_id")
    if not isinstance(session_id, str) or not session_id:
        violations.append("envelope:missing_dispatched_session_id")
        session_id = ""
    if not isinstance(dispatch_id, str) or not dispatch_id:
        violations.append("envelope:missing_dispatch_id")
        dispatch_id = ""
    return session_id, dispatch_id


def _load_index(log_root: Path, violations: list[str]) -> list[dict[str, Any]]:
    index_path = log_root / "sessions.jsonl"
    records = _read_jsonl(index_path, "sessions_index", violations)
    for line_number, row in enumerate(records, 1):
        if not isinstance(row.get("session_id"), str) or not row.get("session_id"):
            violations.append(f"sessions_index:missing_session_id:{line_number}")
    return records


def _safe_session_dir(log_root: Path, row: Mapping[str, Any]) -> Path | None:
    name = row.get("dir_name")
    if not isinstance(name, str) or not name or name in {".", ".."} or Path(name).name != name:
        return None
    return log_root / "sessions" / name


def _read_summary(session_dir: Path, expected_session_id: str) -> dict[str, Any] | None:
    try:
        summary = json.loads((session_dir / "summary.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(summary, dict) or summary.get("session_id") != expected_session_id:
        return None
    identity = summary.get("execution_identity")
    if not isinstance(identity, dict) or not isinstance(identity.get("parent_session_id"), str):
        return None
    return summary


def default_log_root(home: Path) -> Path:
    return home / ".local" / "share" / "autoskillit" / "logs"


def _discover_owned(
    trace_path: Path,
    envelope: dict[str, Any] | None,
    log_root: Path | None,
) -> dict[str, Any]:
    """Resolve only the dispatched session and fresh, linked managed descendants."""
    root = log_root or default_log_root(Path.home())
    violations: list[str] = []
    events = _load_trace(trace_path, violations)
    root_id, dispatch_id = _read_envelope(envelope, violations)
    trace_session_ids = {
        record["session_id"] for record in events if record["event"] in _SESSION_EVENTS
    }
    rows = _load_index(root, violations)
    rows_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        session_id = row.get("session_id")
        if isinstance(session_id, str) and session_id:
            rows_by_id.setdefault(session_id, []).append(row)
    owned: dict[str, dict[str, Any]] = {}
    root_rows = rows_by_id.get(root_id, [])
    if not root_id or len(root_rows) != 1:
        violations.append("sessions_index:root_missing_or_ambiguous")
    else:
        if root_id not in trace_session_ids:
            violations.append("trace:root_session_missing")
        root_row = root_rows[0]
        if not dispatch_id or root_row.get("dispatch_id") != dispatch_id:
            violations.append("sessions_index:root_dispatch_mismatch")
        _add_owned_session(root, root_row, root_id, violations, owned)
    candidates = _fresh_candidates(root, root_id, dispatch_id, trace_session_ids, rows_by_id)
    _add_linked_descendants(root, dispatch_id, candidates, owned, violations)

    return {
        "root_id": root_id,
        "dispatch_id": dispatch_id,
        "log_root": root,
        "events": events,
        "owned": owned,
        "violations": violations,
        "trace_session_ids": trace_session_ids,
    }


def _fresh_candidates(
    log_root: Path,
    root_id: str,
    dispatch_id: str,
    trace_session_ids: set[str],
    rows_by_id: Mapping[str, list[dict[str, Any]]],
) -> dict[str, tuple[dict[str, Any], dict[str, Any] | None]]:
    candidates: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] = {}
    candidate_ids = trace_session_ids | {
        session_id
        for session_id, rows in rows_by_id.items()
        if dispatch_id and any(row.get("dispatch_id") == dispatch_id for row in rows)
    }
    for session_id in candidate_ids:
        if session_id == root_id:
            continue
        matching_rows = rows_by_id.get(session_id, [])
        if len(matching_rows) != 1:
            continue
        row = matching_rows[0]
        session_dir = _safe_session_dir(log_root, row)
        summary = _read_summary(session_dir, session_id) if session_dir else None
        candidates[session_id] = row, summary
    return candidates


def _add_linked_descendants(
    log_root: Path,
    dispatch_id: str,
    candidates: dict[str, tuple[dict[str, Any], dict[str, Any] | None]],
    owned: dict[str, dict[str, Any]],
    violations: list[str],
) -> None:
    remaining = dict(candidates)
    while remaining:
        changed = False
        owned_ids = set(owned)
        for session_id, (row, summary) in list(remaining.items()):
            parents = {
                value
                for key in ("parent_session_id", "caller_session_id")
                if isinstance((value := row.get(key)), str) and value
            }
            summary_parent = summary["execution_identity"]["parent_session_id"] if summary else ""
            if not parents.intersection(owned_ids) and summary_parent not in owned_ids:
                continue
            if not dispatch_id or row.get("dispatch_id") != dispatch_id:
                violations.append(f"sessions_index:{session_id}:dispatch_mismatch")
                del remaining[session_id]
                continue
            if summary is None:
                violations.append(f"summary:{session_id}:missing_or_malformed")
            elif summary_parent not in owned_ids:
                violations.append(f"summary:{session_id}:parent_mismatch")
            if parents and not parents.intersection(owned_ids):
                violations.append(f"sessions_index:{session_id}:parent_mismatch")
            _add_owned_session(log_root, row, session_id, violations, owned, summary=summary)
            del remaining[session_id]
            changed = True
        if not changed:
            break


def _add_owned_session(
    log_root: Path,
    row: Mapping[str, Any],
    session_id: str,
    violations: list[str],
    owned: dict[str, dict[str, Any]],
    *,
    summary: dict[str, Any] | None = None,
) -> None:
    session_dir = _safe_session_dir(log_root, row)
    if session_dir is None:
        violations.append(f"sessions_index:{session_id}:invalid_dir_name")
        return
    required_index_fields = (
        "session_id",
        "dir_name",
        "dispatch_id",
        "parent_session_id",
        "caller_session_id",
        "claude_code_log",
    )
    if any(key not in row for key in required_index_fields):
        violations.append(f"sessions_index:{session_id}:missing_fields")
    if summary is None:
        summary = _read_summary(session_dir, session_id)
    if summary is None:
        violations.append(f"summary:{session_id}:missing_or_malformed")
    elif summary.get("dispatch_id", "") != row.get("dispatch_id", ""):
        violations.append(f"summary:{session_id}:dispatch_mismatch")
    transcript_value = row.get("claude_code_log")
    transcript: Path | None = None
    if isinstance(transcript_value, str) and transcript_value:
        transcript = Path(transcript_value)
        if not transcript.is_absolute():
            transcript = log_root / transcript
    else:
        violations.append(f"sessions_index:{session_id}:missing_transcript")
    if transcript is None or not transcript.is_file():
        violations.append(f"transcript:{session_id}:missing")
    else:
        _read_jsonl(transcript, f"transcript:{session_id}", violations, collect=False)
    owned[session_id] = {
        "row": dict(row),
        "summary": summary,
        "session_dir": session_dir,
        "transcript": transcript,
    }


def _lifecycle_intervals(
    events: list[dict[str, Any]],
    session_ids: set[str],
    violations: list[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    grouped = _group_lifecycle_events(events, session_ids)
    intervals: list[dict[str, Any]] = []
    seen_sessions: set[str] = set()
    for identity, endpoints in grouped.items():
        item = _make_interval(identity, endpoints, violations)
        if item is None:
            continue
        if identity[0] == "session":
            seen_sessions.add(identity[1])
        intervals.append(item)
    return intervals, seen_sessions


def _group_lifecycle_events(
    events: list[dict[str, Any]], session_ids: set[str]
) -> dict[tuple[str, ...], dict[str, list[dict[str, Any]]]]:
    grouped: dict[tuple[str, ...], dict[str, list[dict[str, Any]]]] = {}
    for event in events:
        name = event["event"]
        session_id = event["session_id"]
        if session_id not in session_ids:
            continue
        identity: tuple[str, ...]
        if name in _SESSION_EVENTS:
            identity = ("session", session_id)
            role = "start" if name == "SessionStart" else "end"
        else:
            identity = ("agent", session_id, event["agent_id"])
            role = "start" if name == "SubagentStart" else "end"
        grouped.setdefault(identity, {"start": [], "end": []})[role].append(event)
    return grouped


def _make_interval(
    identity: tuple[str, ...],
    endpoints: Mapping[str, list[dict[str, Any]]],
    violations: list[str],
) -> dict[str, Any] | None:
    starts, stops = endpoints["start"], endpoints["end"]
    label = ":".join(identity)
    if not starts:
        violations.append(f"lifecycle:{label}:orphan_end")
        return None
    if not stops:
        violations.append(f"lifecycle:{label}:missing_end")
        return None
    start = min(float(item["timestamp"]) for item in starts)
    end = max(float(item["timestamp"]) for item in stops)
    if any(float(item["timestamp"]) < start for item in stops):
        violations.append(f"lifecycle:{label}:orphan_end_before_start")
    if end < start or any(float(event["timestamp"]) > end for event in starts):
        reason = "end_before_start" if end < start else "missing_final_end"
        violations.append(f"lifecycle:{label}:{reason}")
        return None
    item = {"kind": identity[0], "session_id": identity[1], "start": start, "end": end}
    if identity[0] == "agent":
        item["agent_id"] = identity[2]
        path_field = "agent_transcript_path"
    else:
        path_field = "transcript_path"
    paths = {
        str(Path(event[path_field]).resolve())
        for event in starts + stops
        if isinstance(event.get(path_field), str) and event[path_field]
    }
    if len(paths) > 1:
        violations.append(f"lifecycle:{label}:transcript_mismatch")
    if paths:
        item["transcript_path"] = sorted(paths)[0]
    for key in ("agent_type", "source"):
        values = {event[key] for event in starts + stops if isinstance(event.get(key), str)}
        if len(values) == 1:
            item[key] = next(iter(values))
    return item


def _check_transcript_coverage(
    events: list[dict[str, Any]],
    owned: Mapping[str, Mapping[str, Any]],
    violations: list[str],
) -> list[dict[str, str]]:
    session_ids = set(owned)
    session_paths: dict[str, set[str]] = {session_id: set() for session_id in session_ids}
    observed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in events:
        session_id = event["session_id"]
        if session_id not in session_ids:
            continue
        if event["event"] in _SESSION_EVENTS and event.get("transcript_path"):
            session_paths[session_id].add(str(Path(event["transcript_path"]).resolve()))
        elif event["event"] in _AGENT_EVENTS:
            key = (session_id, event["agent_id"])
            observed.setdefault(key, []).append(event)
    for session_id, paths in session_paths.items():
        if len(paths) > 1:
            violations.append(f"lifecycle:{session_id}:transcript_mismatch")
    enumerated = _enumerate_native_transcripts(owned, session_paths, violations)

    native_agents: list[dict[str, str]] = []
    agent_keys = set(observed)
    agent_keys.update(
        (session_id, agent_id) for session_id, files in enumerated.items() for agent_id in files
    )
    for session_id, agent_id in sorted(agent_keys):
        transcript = enumerated.get(session_id, {}).get(agent_id)
        proof = _verify_native_transcript(
            session_id, agent_id, transcript, observed.get((session_id, agent_id), []), violations
        )
        if proof is not None:
            native_agents.append(proof)
    return native_agents


def _enumerate_native_transcripts(
    owned: Mapping[str, Mapping[str, Any]],
    session_paths: Mapping[str, set[str]],
    violations: list[str],
) -> dict[str, dict[str, Path]]:
    enumerated: dict[str, dict[str, Path]] = {}
    for session_id, item in owned.items():
        transcript = item.get("transcript")
        if not isinstance(transcript, Path) or not transcript.is_file():
            continue
        paths = session_paths.get(session_id, set())
        if not paths:
            violations.append(f"lifecycle:{session_id}:transcript_path_missing")
        elif paths != {str(transcript.resolve())}:
            violations.append(f"lifecycle:{session_id}:transcript_path_mismatch")
        subagents_dir = transcript.parent / session_id / "subagents"
        files: dict[str, Path] = {}
        for path in sorted(subagents_dir.glob("agent-*.jsonl")):
            agent_id = path.name.removeprefix("agent-").removesuffix(".jsonl")
            if not agent_id:
                continue
            files[agent_id] = path
            if not path.is_file():
                violations.append(f"native:{session_id}:{agent_id}:transcript_missing")
        enumerated[session_id] = files
    return enumerated


def _verify_native_transcript(
    session_id: str,
    agent_id: str,
    transcript: Path | None,
    events: list[dict[str, Any]],
    violations: list[str],
) -> dict[str, str] | None:
    label = f"native:{session_id}:{agent_id}"
    names = {event["event"] for event in events}
    if not {"SubagentStart", "SubagentStop"} <= names:
        violations.append(f"{label}:lifecycle_incomplete")
    if transcript is None:
        violations.append(f"{label}:transcript_unenumerated")
        return None
    expected_path = str(transcript.resolve())
    paths = {
        str(Path(event["agent_transcript_path"]).resolve())
        for event in events
        if isinstance(event.get("agent_transcript_path"), str)
    }
    if paths != {expected_path}:
        violations.append(f"{label}:transcript_mismatch")
    if not paths:
        violations.append(f"{label}:transcript_path_missing")
    if not transcript.is_file():
        violations.append(f"{label}:transcript_missing")
        return None
    _read_jsonl(transcript, f"transcript:{session_id}:{agent_id}", violations, collect=False)
    return {"session_id": session_id, "agent_id": agent_id, "transcript_path": expected_path}


def _peak_sessions(intervals: list[dict[str, Any]]) -> int:
    points: list[tuple[float, int]] = []
    for item in intervals:
        if item["start"] == item["end"]:
            continue
        points.append((item["end"], -1))
        points.append((item["start"], 1))
    active = peak = 0
    for _timestamp, delta in sorted(points, key=lambda point: (point[0], point[1])):
        active += delta
        peak = max(peak, active)
    return peak


def summarize(
    trace_path: Path,
    envelope: dict[str, Any] | None,
    log_root: Path | None = None,
) -> dict[str, Any]:
    """Write a bounded concurrency summary beside the raw lifecycle trace."""
    context = _discover_owned(trace_path, envelope, log_root)
    violations = list(context["violations"])
    owned: dict[str, dict[str, Any]] = context["owned"]
    owned_ids = set(owned)
    intervals, seen_sessions = _lifecycle_intervals(context["events"], owned_ids, violations)
    for session_id in sorted(owned_ids - seen_sessions):
        violations.append(f"lifecycle:{session_id}:missing_endpoints")
    native_agents = _check_transcript_coverage(context["events"], owned, violations)
    root_id = context["root_id"]
    root_seen = root_id in seen_sessions
    summary = {
        "method": "hook_lifecycle_intervals",
        "trace_path": str(trace_path),
        "peak_sessions": _peak_sessions(intervals),
        "identities": sorted(
            intervals,
            key=lambda item: (
                item["start"],
                item["kind"],
                item["session_id"],
                item.get("agent_id", ""),
            ),
        ),
        "coverage": {
            "complete": not violations,
            "root_session_id": root_id or None,
            "root_session_seen": root_seen,
            "managed_session_ids": sorted(owned_ids - ({root_id} if root_id else set())),
            "owned_session_ids": sorted(owned_ids),
            "native_agents": native_agents,
        },
        "violations": sorted(set(violations)),
    }
    output_path = trace_path.with_name("session-concurrency.json")
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def _issue_number(issue_url: str) -> str:
    match = re.search(r"/issues/(\d+)/?$", urlsplit(issue_url).path)
    if match:
        return match.group(1)
    match = re.search(r"#(\d+)$", issue_url)
    return match.group(1) if match else ""


def _issue_matches_repository(issue_url: str, repository: str) -> bool:
    parsed = urlsplit(issue_url)
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "github.com"
        and (parsed.path.strip("/").split("/issues/", 1)[0].lower() == repository.lower())
    )


def _remote_matches_repository(remote_url: Any, repository: str) -> bool:
    if not isinstance(remote_url, str):
        return False
    https = re.fullmatch(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?", remote_url)
    ssh = re.fullmatch(r"git@github\.com:([^/]+/[^/]+)\.git", remote_url)
    match = https or ssh
    return bool(match and match.group(1).lower() == repository.lower())


def _result_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if isinstance(value, dict):
        if any(
            key in value for key in ("success", "claimed", "merge_target", "error", "error_type")
        ):
            return value
        nested = value.get("structuredContent", value.get("result", value.get("content")))
        if isinstance(nested, list):
            return _result_text_blocks(nested)
        elif isinstance(nested, str):
            return _result_object(nested)
    if isinstance(value, list):
        return _result_text_blocks(value)
    return None


def _result_text_blocks(blocks: list[Any]) -> dict[str, Any] | None:
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            continue
        try:
            decoded = json.loads(block["text"])
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            return decoded
    return None


def _transcript_tool_pairs(
    transcript: Path,
    violation_prefix: str,
    violations: list[str],
) -> list[tuple[str, dict[str, Any], dict[str, Any] | None, bool]]:
    """Return paired target calls as name, input, result, and result-error flag."""
    try:
        handle = transcript.open(encoding="utf-8", errors="replace")
    except OSError:
        violations.append(f"transcript:{violation_prefix}:unavailable")
        return []
    pending: dict[str, tuple[str, dict[str, Any]]] = {}
    paired: list[tuple[str, dict[str, Any], dict[str, Any] | None, bool]] = []
    with handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                violations.append(f"transcript:{violation_prefix}:invalid_json:{line_number}")
                continue
            if isinstance(row, dict):
                _pair_record(row, line_number, violation_prefix, pending, paired, violations)
    for tool_id, (name, tool_input) in pending.items():
        violations.append(f"transcript:{violation_prefix}:unpaired_tool_use:{tool_id}")
        paired.append((name, tool_input, None, False))
    return paired


def _pair_record(
    row: dict[str, Any],
    line_number: int,
    violation_prefix: str,
    pending: dict[str, tuple[str, dict[str, Any]]],
    paired: list[tuple[str, dict[str, Any], dict[str, Any] | None, bool]],
    violations: list[str],
) -> None:
    message = row.get("message")
    if not isinstance(message, dict):
        message = row
    content = message.get("content", row.get("content", []))
    if not isinstance(content, list):
        return
    role = row.get("type", message.get("role"))
    for block in content:
        if not isinstance(block, dict):
            continue
        if role == "assistant" and block.get("type") == "tool_use":
            _remember_tool_use(block, line_number, violation_prefix, pending, violations)
        elif role == "user" and block.get("type") == "tool_result":
            tool_id = block.get("tool_use_id")
            if isinstance(tool_id, str) and tool_id in pending:
                name, tool_input = pending.pop(tool_id)
                result = _result_object(block.get("content"))
                paired.append((name, tool_input, result, block.get("is_error") is True))


def _remember_tool_use(
    block: dict[str, Any],
    line_number: int,
    prefix: str,
    pending: dict[str, tuple[str, dict[str, Any]]],
    violations: list[str],
) -> None:
    name = block.get("name")
    if not isinstance(name, str):
        return
    name = name.removeprefix("mcp__autoskillit__")
    if name not in {"claim_and_resolve_issue", "create_and_publish_branch"}:
        return
    tool_id, tool_input = block.get("id"), block.get("input")
    if not isinstance(tool_id, str) or not tool_id or not isinstance(tool_input, dict):
        violations.append(f"transcript:{prefix}:malformed_tool_use:{line_number}")
        return
    if tool_id in pending:
        violations.append(f"transcript:{prefix}:duplicate_tool_use:{line_number}")
    pending[tool_id] = (name, tool_input)


def collect_ownership(
    trace_path: Path,
    envelope: dict[str, Any] | None,
    log_root: Path | None = None,
    issue_url: str = "",
    repository: str = "",
    claim_label: str | None = None,
) -> dict[str, Any]:
    """Collect compact branch and claim proofs from the owned transcript lineage."""
    context = _discover_owned(trace_path, envelope, log_root)
    violations = list(context["violations"])
    root_id = context["root_id"]
    expected_number = _issue_number(issue_url)
    valid_issue_reference = bool(
        expected_number and repository and _issue_matches_repository(issue_url, repository)
    )
    if not valid_issue_reference:
        violations.append("ownership:invalid_issue_reference")

    branches, claim_calls, transcript_evidence = _owned_tool_proofs(
        context["owned"],
        root_id,
        valid_issue_reference,
        expected_number,
        issue_url,
        repository,
        violations,
    )
    claimed = _claim_succeeded(claim_calls, expected_number, claim_label, violations)
    return {
        "branches": sorted(set(branches)),
        "claimed": claimed,
        "transcripts": sorted(transcript_evidence, key=lambda item: item["session_id"]),
        "violations": sorted(set(violations)),
    }


def _owned_tool_proofs(
    owned: Mapping[str, Mapping[str, Any]],
    root_id: str,
    valid_issue_reference: bool,
    expected_number: str,
    issue_url: str,
    repository: str,
    violations: list[str],
) -> tuple[
    list[str], list[tuple[dict[str, Any], dict[str, Any] | None, bool]], list[dict[str, str]]
]:
    branches: list[str] = []
    claims: list[tuple[dict[str, Any], dict[str, Any] | None, bool]] = []
    transcripts: list[dict[str, str]] = []
    for session_id, item in owned.items():
        transcript = item.get("transcript")
        if not isinstance(transcript, Path) or not transcript.is_file():
            continue
        transcripts.append({"session_id": session_id, "path": str(transcript)})
        for name, tool_input, result, is_error in _transcript_tool_pairs(
            transcript, session_id, violations
        ):
            target = _branch_target(
                session_id,
                root_id,
                name,
                tool_input,
                result,
                is_error,
                valid_issue_reference,
                expected_number,
                repository,
                violations,
            )
            if target is not None:
                branches.append(target)
            if name == "claim_and_resolve_issue" and valid_issue_reference:
                if tool_input.get("issue_url") == issue_url:
                    claims.append((tool_input, result, is_error))
    return branches, claims, transcripts


def _branch_target(
    session_id: str,
    root_id: str,
    name: str,
    tool_input: dict[str, Any],
    result: dict[str, Any] | None,
    is_error: bool,
    valid_issue_reference: bool,
    expected_number: str,
    repository: str,
    violations: list[str],
) -> str | None:
    if name != "create_and_publish_branch" or session_id != root_id or not valid_issue_reference:
        return None
    if tool_input.get("issue_number") != expected_number:
        return None
    if not _remote_matches_repository(tool_input.get("remote_url"), repository):
        return None
    if (
        is_error
        or result is None
        or "error" in result
        or "error_type" in result
        or not isinstance(result.get("merge_target"), str)
        or not result["merge_target"].strip()
    ):
        violations.append("ownership:branch_result_failed")
        return None
    return result["merge_target"].strip()


def _claim_succeeded(
    claims: list[tuple[dict[str, Any], dict[str, Any] | None, bool]],
    expected_number: str,
    claim_label: str | None,
    violations: list[str],
) -> bool:
    if len(claims) > 1:
        violations.append("ownership:claim_reentry")
        return False
    if not claims:
        return False
    tool_input, result, is_error = claims[0]
    requested_label = tool_input.get("label")
    label_matches_request = claim_label is None or requested_label in (None, "", claim_label)
    claimed = bool(
        not is_error
        and label_matches_request
        and tool_input.get("allow_reentry") is not True
        and result is not None
        and result.get("success") is True
        and result.get("claimed") is True
        and str(result.get("issue_number", "")) == expected_number
        and result.get("reentry") is not True
    )
    if not claimed:
        violations.append("ownership:claim_result_failed")
    return claimed


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: e2e_sessions.py <trace>", file=sys.stderr)
        return 2
    try:
        raw = sys.stdin.buffer.read(_MAX_HOOK_INPUT_BYTES + 1)
        if len(raw) > _MAX_HOOK_INPUT_BYTES:
            raise ValueError("lifecycle hook input exceeds the size limit")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("lifecycle hook input must be an object")
        capture_hook_event(Path(args[0]), payload)
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"e2e lifecycle capture failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
