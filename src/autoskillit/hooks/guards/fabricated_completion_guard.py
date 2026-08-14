#!/usr/bin/env python3
"""Deny assistant-authored background completion notifications while work is live.

The server receipt handshake is the completion authority. This guard is only a
defense-in-depth check for a narrowly identifiable fabricated notification in the
current parent orchestrator transcript. Missing or ambiguous provenance fails open.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

FABRICATED_COMPLETION_DENY_TRIGGER: str = "FABRICATED BACKGROUND COMPLETION"

_MAX_TRANSCRIPT_TAIL_BYTES = 256 * 1024
_MAX_MARKER_AGE_SECONDS = 90.0
_MAX_FUTURE_MARKER_SKEW_SECONDS = 5.0
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_ELEMENT_OPEN_RE = re.compile(
    r"<(?P<name>bg_result|task-notification|task_notification)"
    r"(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*"
    r"(?:\s*=\s*(?:\"[^\"<>]*\"|'[^'<>]*'|[^\s\"'=<>`]+))?)*\s*>",
    re.IGNORECASE,
)
_TERMINAL_STATUS_RE = re.compile(
    r"<status>\s*(?:completed|failed|cancelled)\s*</status>",
    re.IGNORECASE,
)


def _bounded_tail(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - _MAX_TRANSCRIPT_TAIL_BYTES))
            raw = stream.read(_MAX_TRANSCRIPT_TAIL_BYTES)
    except OSError:
        return None
    if size > _MAX_TRANSCRIPT_TAIL_BYTES:
        _, separator, raw = raw.partition(b"\n")
        if not separator:
            return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _content_text(content: object) -> tuple[bool, str | None]:
    if isinstance(content, str):
        return True, content
    if not isinstance(content, list):
        return False, None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            return False, None
        block_type = block.get("type")
        if block_type == "tool_use":
            continue
        text = block.get("text")
        if block_type not in ("text", "output_text") or not isinstance(text, str):
            return False, None
        parts.append(text)
    return True, "".join(parts) if parts else None


def _claude_turn_key(
    record: dict[str, Any], message: dict[str, Any]
) -> tuple[bool, tuple[str, ...] | None]:
    request_present = "requestId" in record
    message_present = "id" in message
    request_id = record.get("requestId")
    message_id = message.get("id")
    if request_present and (not isinstance(request_id, str) or not request_id):
        return False, None
    if message_present and (not isinstance(message_id, str) or not message_id):
        return False, None
    if request_present and message_present:
        assert isinstance(request_id, str)
        assert isinstance(message_id, str)
        return True, ("both", request_id, message_id)
    if request_present:
        assert isinstance(request_id, str)
        return True, ("request", request_id)
    if message_present:
        assert isinstance(message_id, str)
        return True, ("message", message_id)
    return True, None


def _newest_logical_turn_assistant_text(path: Path, session_id: str) -> str | None:
    tail = _bounded_tail(path)
    if tail is None:
        return None
    records: list[dict[str, Any]] = []
    for line in tail.splitlines():
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(record, dict):
            return None
        records.append(record)

    candidate_found = False
    candidate_key: tuple[str, ...] | None = None
    parts: list[str] = []
    for record in reversed(records):
        record_type = record.get("type")
        if record_type in {"assistant", "user", "system", "tool"}:
            message = record.get("message")
            if not isinstance(message, dict):
                if record_type == "system" and isinstance(record.get("subtype"), str):
                    continue
                return None
            role = message.get("role")
            if role not in {"assistant", "user", "system", "tool"}:
                return None
            if role != "assistant":
                if candidate_found:
                    break
                return None
            record_session = record.get("session_id", record.get("sessionId"))
            if (
                (record_session is not None and record_session != session_id)
                or record.get("isSidechain") is True
                or record.get("isMeta") is True
                or record.get("agent_id")
                or record.get("agentId")
            ):
                if candidate_found:
                    break
                continue
            valid_key, logical_key = _claude_turn_key(record, message)
            if not valid_key:
                return None
            if candidate_found and (
                candidate_key is None or logical_key is None or logical_key != candidate_key
            ):
                break
            if not candidate_found:
                candidate_found = True
                candidate_key = logical_key
            valid_content, text = _content_text(message.get("content"))
            if not valid_content:
                return None
            if text is not None:
                parts.append(text)
            continue
        if record_type == "response_item":
            payload = record.get("payload")
            if not isinstance(payload, dict):
                return None
            payload_type = payload.get("type")
            if payload_type in {"function_call_output", "custom_tool_call_output"}:
                if candidate_found:
                    break
                return None
            if payload_type == "function_call":
                continue
            if payload_type == "message":
                role = payload.get("role")
                if role not in {"assistant", "user", "system", "tool"}:
                    return None
                if role != "assistant":
                    if candidate_found:
                        break
                    return None
                record_session = record.get("session_id", record.get("sessionId"))
                if (
                    (record_session is not None and record_session != session_id)
                    or payload.get("agent_id")
                    or payload.get("agentId")
                ):
                    if candidate_found:
                        break
                    continue
                if candidate_found:
                    break
                candidate_found = True
                valid_content, text = _content_text(payload.get("content"))
                if not valid_content:
                    return None
                if text is not None:
                    parts.append(text)
                continue
            if not isinstance(payload_type, str):
                return None
            continue
        if not isinstance(record_type, str):
            return None
    if not candidate_found or not parts:
        return None
    return "".join(reversed(parts))


def _has_fresh_matching_marker(transcript: Path, session_id: str) -> bool:
    if not _SESSION_ID_RE.fullmatch(session_id):
        return False
    try:
        candidates = transcript.parent.glob(f"run-skill-in-progress-{session_id}-*.marker")
        now = time.time()
        for marker in candidates:
            try:
                age = now - marker.stat().st_mtime
                if age < -_MAX_FUTURE_MARKER_SKEW_SECONDS or age > _MAX_MARKER_AGE_SECONDS:
                    continue
                payload = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if (
                isinstance(payload, dict)
                and payload.get("label") == "run-skill"
                and payload.get("session_id") == session_id
            ):
                return True
    except OSError:
        return False
    return False


def _is_fabricated_completion(text: str) -> bool:
    position = 0
    blocked_names: set[str] = set()
    while opener := _ELEMENT_OPEN_RE.search(text, position):
        name = opener.group("name").lower()
        if name in blocked_names:
            position = opener.end()
            continue
        closer = f"</{name}>"
        close_match = re.search(re.escape(closer), text[opener.end() :], re.IGNORECASE)
        if close_match is None:
            blocked_names.add(name)
            position = opener.end()
            continue
        close_start = opener.end() + close_match.start()

        nested = _ELEMENT_OPEN_RE.search(text, opener.end())
        while nested is not None and nested.start() < close_start:
            if nested.group("name").lower() == name:
                break
            nested = _ELEMENT_OPEN_RE.search(text, nested.end())
        if nested is not None and nested.start() < close_start:
            position = close_start + len(closer)
            continue

        body = text[opener.end() : close_start]
        position = close_start + len(closer)
        if not body.strip():
            continue
        if name == "bg_result" or _TERMINAL_STATUS_RE.search(body):
            return True
    return False


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        return
    if not isinstance(data, dict):
        return
    if (
        os.environ.get("AUTOSKILLIT_SESSION_TYPE") != "orchestrator"
        or data.get("agent_id")
        or data.get("agentId")
    ):
        return

    session_id = data.get("session_id")
    transcript_path = data.get("transcript_path")
    if not isinstance(session_id, str) or not isinstance(transcript_path, str):
        return
    transcript = Path(transcript_path)
    if not _has_fresh_matching_marker(transcript, session_id):
        return
    assistant_text = _newest_logical_turn_assistant_text(transcript, session_id)
    if assistant_text is None or not _is_fabricated_completion(assistant_text):
        return

    reason = (
        f"{FABRICATED_COMPLETION_DENY_TRIGGER}. Wait for the actual run_skill tool "
        "result, then acknowledge its exact receipt_id with complete_run_skill_result."
    )
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
