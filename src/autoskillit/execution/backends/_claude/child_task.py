"""Bounded parser for one Claude delegated-child transcript."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict

from autoskillit.core import ChildTaskTranscript, is_valid_child_task_id

CLAUDE_CHILD_TRANSCRIPT_MAX_BYTES = 64 * 1024 * 1024


class _AssistantGroup(TypedDict):
    text: list[str]
    stop_reason: str | None
    synthetic: bool


def _text_blocks(content: object) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [
        block["text"]
        for block in content
        if isinstance(block, Mapping)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]


def _assignment_label(path: Path, child_id: str) -> str:
    meta_path = path.with_name(f"agent-{child_id}.meta.json")
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if isinstance(metadata, Mapping) and isinstance(metadata.get("description"), str):
        return metadata["description"]
    return ""


def _read_claude_records(path: Path) -> list[Mapping[str, Any]] | None:
    """Read and decode JSONL records within the transcript byte limit."""
    try:
        if path.stat().st_size > CLAUDE_CHILD_TRANSCRIPT_MAX_BYTES:
            return None
        with path.open("rb") as source:
            raw = source.read(CLAUDE_CHILD_TRANSCRIPT_MAX_BYTES + 1)
    except OSError:
        return None
    if len(raw) > CLAUDE_CHILD_TRANSCRIPT_MAX_BYTES:
        return None

    records: list[Mapping[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, Mapping):
            records.append(record)
    return records


def _assignment_prompt(records: list[Mapping[str, Any]]) -> str:
    for record in records:
        if record.get("type") != "user":
            continue
        message = record.get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        return "\n".join(_text_blocks(content))
    return ""


def _assistant_groups(
    records: list[Mapping[str, Any]],
) -> tuple[list[_AssistantGroup], int | None, str | None]:
    groups: list[_AssistantGroup] = []
    groups_by_message_id: dict[str, int] = {}
    last_assistant_group_index: int | None = None
    last_relevant_record_type: str | None = None

    for record in records:
        record_type = record.get("type")
        if record_type == "user":
            last_relevant_record_type = "user"
            last_assistant_group_index = None
            continue
        if record_type != "assistant":
            continue

        message = record.get("message")
        if not isinstance(message, Mapping):
            message = {}
        message_id = message.get("id")
        if isinstance(message_id, str):
            group_index = groups_by_message_id.get(message_id)
            if group_index is None:
                group_index = len(groups)
                groups_by_message_id[message_id] = group_index
                groups.append({"text": [], "stop_reason": None, "synthetic": False})
        else:
            group_index = len(groups)
            groups.append({"text": [], "stop_reason": None, "synthetic": False})

        group = groups[group_index]
        group["text"].extend(_text_blocks(message.get("content")))
        stop_reason = message.get("stop_reason")
        if isinstance(stop_reason, str):
            group["stop_reason"] = stop_reason
        if message.get("model") == "<synthetic>":
            group["synthetic"] = True
        last_assistant_group_index = group_index
        last_relevant_record_type = "assistant"

    return groups, last_assistant_group_index, last_relevant_record_type


def _final_group_summary(
    groups: list[_AssistantGroup],
    last_assistant_group_index: int | None,
    last_relevant_record_type: str | None,
) -> tuple[bool, str | None, str, int]:
    output_limit_stops = sum(group["stop_reason"] == "max_tokens" for group in groups)
    final_group = groups[-1] if groups else None
    final_text = None
    final_stop_reason = ""
    final_group_is_synthetic = False
    if final_group is not None:
        joined_text = "\n".join(final_group["text"])
        final_text = joined_text or None
        final_group_is_synthetic = final_group["synthetic"]
        group_stop_reason = final_group["stop_reason"]
        final_stop_reason = (
            group_stop_reason
            if group_stop_reason is not None
            else ("synthetic" if final_group_is_synthetic else "")
        )
    terminal = (
        final_group is not None
        and final_stop_reason in {"end_turn", "stop_sequence"}
        and not final_group_is_synthetic
        and last_relevant_record_type == "assistant"
        and last_assistant_group_index == len(groups) - 1
    )
    return terminal, final_text, final_stop_reason, output_limit_stops


def parse_claude_child_task(path: Path, child_id: str) -> ChildTaskTranscript | None:
    """Return the delegated task's prompt and last assistant turn when readable."""
    if not is_valid_child_task_id(child_id):
        return None
    records = _read_claude_records(path)
    if records is None:
        return None
    assignment_prompt = _assignment_prompt(records)
    groups, last_group_index, last_record_type = _assistant_groups(records)
    terminal, final_text, final_stop_reason, output_limit_stops = _final_group_summary(
        groups, last_group_index, last_record_type
    )

    return ChildTaskTranscript(
        child_id=child_id,
        transcript_locator=str(path),
        assignment_prompt=assignment_prompt,
        assignment_label=_assignment_label(path, child_id),
        terminal=terminal,
        final_text=final_text,
        final_stop_reason=final_stop_reason,
        output_limit_stops=output_limit_stops,
    )


__all__ = ["CLAUDE_CHILD_TRANSCRIPT_MAX_BYTES", "parse_claude_child_task"]
