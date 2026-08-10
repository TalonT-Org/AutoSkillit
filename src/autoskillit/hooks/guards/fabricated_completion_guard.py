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
_BG_RESULT_RE = re.compile(
    r"\s*<bg_result(?:\s+[^>]*)?>.+?</bg_result>\s*", re.IGNORECASE | re.DOTALL
)
_TASK_NOTIFICATION_RE = re.compile(
    r"\s*<task[-_]notification(?:\s+[^>]*)?>"
    r"(?=[\s\S]*?<status>\s*(?:completed|failed|cancelled)\s*</status>)"
    r"[\s\S]+?</task[-_]notification>\s*",
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


def _content_text(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            return None
        block_type = block.get("type")
        text = block.get("text")
        if block_type not in ("text", "output_text") or not isinstance(text, str):
            return None
        parts.append(text)
    return "".join(parts)


def _assistant_text(record: dict[str, Any], session_id: str) -> str | None:
    record_session = record.get("session_id", record.get("sessionId"))
    if record_session is not None and record_session != session_id:
        return None

    if record.get("type") == "assistant":
        message = record.get("message")
        if (
            not isinstance(message, dict)
            or message.get("role") != "assistant"
            or record.get("isSidechain") is True
            or record.get("isMeta") is True
            or record.get("agent_id")
            or record.get("agentId")
        ):
            return None
        return _content_text(message.get("content"))

    if record.get("type") == "response_item":
        payload = record.get("payload")
        if (
            not isinstance(payload, dict)
            or payload.get("type") != "message"
            or payload.get("role") != "assistant"
            or payload.get("agent_id")
            or payload.get("agentId")
        ):
            return None
        return _content_text(payload.get("content"))
    return None


def _newest_parent_assistant_text(path: Path, session_id: str) -> str | None:
    tail = _bounded_tail(path)
    if tail is None:
        return None
    newest: str | None = None
    for line in tail.splitlines():
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(record, dict):
            return None
        if record.get("type") in {"assistant", "user", "system", "tool"}:
            message = record.get("message")
            if not isinstance(message, dict) or message.get("role") not in {
                "assistant",
                "user",
                "system",
                "tool",
            }:
                return None
            newest = _assistant_text(record, session_id)
            continue
        if record.get("type") == "response_item":
            payload = record.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "message":
                if payload.get("role") not in {"assistant", "user", "system", "tool"}:
                    return None
                newest = _assistant_text(record, session_id)
    return newest


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
    return bool(_BG_RESULT_RE.fullmatch(text) or _TASK_NOTIFICATION_RE.fullmatch(text))


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        return
    if not isinstance(data, dict):
        return
    if (
        os.environ.get("AUTOSKILLIT_HEADLESS") != "1"
        or os.environ.get("AUTOSKILLIT_SESSION_TYPE") != "orchestrator"
        or data.get("agent_id")
    ):
        return

    session_id = data.get("session_id")
    transcript_path = data.get("transcript_path")
    if not isinstance(session_id, str) or not isinstance(transcript_path, str):
        return
    transcript = Path(transcript_path)
    if not _has_fresh_matching_marker(transcript, session_id):
        return
    assistant_text = _newest_parent_assistant_text(transcript, session_id)
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
