"""Parser for one Codex delegated-child rollout."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from autoskillit.core import ChildTaskTranscript
from autoskillit.execution.backends._codex_execution_identity import read_codex_rollout_events


def _last_path_segment(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return next((part for part in reversed(value.split("/")) if part), "")


def parse_codex_child_task(path: Path, child_id: str) -> ChildTaskTranscript | None:
    """Return one child's prompt, label, final message, and lifecycle state."""
    events = read_codex_rollout_events(path)
    first_meta_event = next(
        (event for event in events if event.get("type") == "session_meta"), None
    )
    first_meta = first_meta_event.get("payload") if first_meta_event is not None else None
    if not isinstance(first_meta, Mapping) or first_meta.get("id") != child_id:
        return None

    source = first_meta.get("source")
    subagent = source.get("subagent") if isinstance(source, Mapping) else None
    thread_spawn = subagent.get("thread_spawn") if isinstance(subagent, Mapping) else None
    source_agent_path = (
        thread_spawn.get("agent_path") if isinstance(thread_spawn, Mapping) else None
    )
    assignment_label = _last_path_segment(source_agent_path) or _last_path_segment(
        first_meta.get("agent_path")
    )

    prompt_parts: list[str] = []
    for event in events:
        payload = event.get("payload")
        if (
            event.get("type") != "response_item"
            or not isinstance(payload, Mapping)
            or payload.get("type") != "message"
            or payload.get("role") != "user"
        ):
            continue
        content = payload.get("content")
        if not isinstance(content, list):
            continue
        prompt_parts.extend(
            block["text"]
            for block in content
            if isinstance(block, Mapping)
            and block.get("type") in {"input_text", "text"}
            and isinstance(block.get("text"), str)
        )

    last_lifecycle_type = ""
    last_lifecycle_payload: Mapping[str, Any] | None = None
    for event in events:
        payload = event.get("payload")
        if event.get("type") != "event_msg" or not isinstance(payload, Mapping):
            continue
        event_type = payload.get("type")
        if event_type in {"task_started", "task_complete", "turn_aborted"}:
            last_lifecycle_type = event_type
            last_lifecycle_payload = payload

    last_agent_message = (
        last_lifecycle_payload.get("last_agent_message")
        if last_lifecycle_payload is not None
        else None
    )
    return ChildTaskTranscript(
        child_id=child_id,
        transcript_locator=str(path),
        assignment_prompt="\n".join(prompt_parts),
        assignment_label=assignment_label,
        terminal=last_lifecycle_type == "task_complete",
        final_text=(
            last_agent_message
            if isinstance(last_agent_message, str) and last_agent_message
            else None
        ),
        final_stop_reason=last_lifecycle_type,
        output_limit_stops=0,
    )


__all__ = ["parse_codex_child_task"]
