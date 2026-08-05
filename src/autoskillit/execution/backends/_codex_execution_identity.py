"""Authoritative execution identity extraction from Codex rollout records."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import zstandard

from autoskillit.core import AGENT_BACKEND_CODEX, ChildExecutionIdentity, ExecutionIdentity

_MAX_ROLLOUT_IDENTITY_BYTES = 16 * 1024 * 1024


def _read_rollout(path: Path) -> list[Mapping[str, Any]]:
    if path.name.endswith(".zst"):
        try:
            with path.open("rb") as source:
                with zstandard.ZstdDecompressor().stream_reader(source) as reader:
                    raw = reader.read(_MAX_ROLLOUT_IDENTITY_BYTES + 1)
        except zstandard.ZstdError as exc:
            raise ValueError("invalid compressed Codex rollout") from exc
    else:
        with path.open("rb") as source:
            raw = source.read(_MAX_ROLLOUT_IDENTITY_BYTES + 1)
    if len(raw) > _MAX_ROLLOUT_IDENTITY_BYTES:
        raise ValueError("Codex rollout exceeds the bounded identity extraction limit")
    events: list[Mapping[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, Mapping):
            events.append(value)
    return events


def _payloads(events: list[Mapping[str, Any]], event_type: str) -> list[Mapping[str, Any]]:
    return [
        payload
        for event in events
        if event.get("type") == event_type
        and isinstance((payload := event.get("payload")), Mapping)
    ]


def _unique_text(owner: str, field: str, values: list[object], *, required: bool) -> str:
    observed = {value for value in values if isinstance(value, str) and value}
    if len(observed) > 1:
        raise ValueError(f"Codex {owner} rollout has conflicting {field} values")
    if observed:
        return next(iter(observed))
    if required:
        raise ValueError(f"Codex {owner} rollout omitted {field}")
    return ""


def _spawn_record(meta: Mapping[str, Any]) -> Mapping[str, Any]:
    source = meta.get("source")
    if not isinstance(source, Mapping):
        return {}
    subagent = source.get("subagent")
    if not isinstance(subagent, Mapping):
        return {}
    spawn = subagent.get("thread_spawn")
    return spawn if isinstance(spawn, Mapping) else {}


def _meta_text(meta: Mapping[str, Any], spawn: Mapping[str, Any], field: str) -> str:
    value = meta.get(field) or spawn.get(field)
    return value if isinstance(value, str) else ""


def _instruction_text(meta: Mapping[str, Any]) -> str:
    base = meta.get("base_instructions")
    if isinstance(base, str):
        return base
    if isinstance(base, Mapping) and isinstance(base.get("text"), str):
        return str(base["text"])
    return ""


def _message_text(events: list[Mapping[str, Any]]) -> str:
    blocks: list[str] = []
    for payload in _payloads(events, "response_item"):
        if payload.get("type") != "message":
            continue
        content = payload.get("content")
        if isinstance(content, str):
            blocks.append(content)
        elif isinstance(content, list):
            blocks.extend(
                str(block.get("text", "")) for block in content if isinstance(block, Mapping)
            )
    return "\n".join(blocks)


def extract_codex_execution_identity(
    parent_rollout_path: Path,
    *,
    requested: ExecutionIdentity,
    child_rollout_paths: tuple[Path, ...] | None = None,
    child_rollout_resolver: Callable[[str], Path | None] | None = None,
) -> ExecutionIdentity:
    """Merge Codex-owned effective identity into immutable requested launch intent.

    Generated configuration is never consulted. Parent/child IDs, role, CLI,
    model, and effort are accepted only from ``session_meta`` and
    ``turn_context`` records. A child rollout must link back to the exact parent.
    """
    parent_events = _read_rollout(parent_rollout_path)
    parent_metas = _payloads(parent_events, "session_meta")
    if len(parent_metas) != 1:
        raise ValueError("Codex parent rollout must contain exactly one session_meta")
    parent_meta = parent_metas[0]
    parent_id = _meta_text(parent_meta, {}, "id")
    if not parent_id:
        raise ValueError("Codex parent session_meta omitted id")
    if requested.parent_session_id and requested.parent_session_id != parent_id:
        raise ValueError("Codex parent rollout identity disagrees with requested linkage")
    parent_contexts = _payloads(parent_events, "turn_context")
    if not parent_contexts:
        raise ValueError("Codex parent rollout omitted turn_context")
    parent_model = _unique_text(
        "parent", "model", [context.get("model") for context in parent_contexts], required=True
    )
    parent_effort = _unique_text(
        "parent", "effort", [context.get("effort") for context in parent_contexts], required=False
    )
    cli_version = _meta_text(parent_meta, {}, "cli_version")

    effective = replace(
        requested,
        effective_parent_backend=AGENT_BACKEND_CODEX,
        effective_parent_model=parent_model,
        effective_parent_effort=parent_effort,
        cli_version=cli_version,
        parent_session_id=parent_id,
    )
    if not requested.children:
        return effective
    linked_child_ids = tuple(
        sorted(
            {
                str(payload["agent_thread_id"])
                for payload in _payloads(parent_events, "event_msg")
                if payload.get("type") == "sub_agent_activity"
                and payload.get("kind") == "started"
                and isinstance(payload.get("agent_thread_id"), str)
                and payload.get("agent_thread_id") != parent_id
            }
        )
    )
    if len(linked_child_ids) != len(requested.children):
        raise ValueError("Codex parent rollout child count disagrees with requested plan")
    if child_rollout_paths is None:
        if child_rollout_resolver is None:
            raise ValueError("Codex child rollout resolver is required")
        resolved_paths = tuple(child_rollout_resolver(child_id) for child_id in linked_child_ids)
        if any(path is None for path in resolved_paths):
            raise ValueError("Codex linked child rollout could not be resolved")
        child_rollout_paths = tuple(path for path in resolved_paths if path is not None)
    if len(child_rollout_paths) != len(linked_child_ids):
        raise ValueError("Codex child rollout path count disagrees with parent linkage")

    observed: dict[str, ChildExecutionIdentity] = {}
    child_cli_versions: set[str] = set()
    for child_rollout_path in child_rollout_paths:
        child_events = _read_rollout(child_rollout_path)
        child_metas = _payloads(child_events, "session_meta")
        if len(child_metas) != 1:
            raise ValueError("Codex child rollout must contain exactly one session_meta")
        child_meta = child_metas[0]
        spawn = _spawn_record(child_meta)
        linked_parent = _meta_text(child_meta, spawn, "forked_from_id") or _meta_text(
            child_meta, spawn, "parent_thread_id"
        )
        if linked_parent != parent_id:
            raise ValueError("Codex child rollout is not linked to the authoritative parent")
        child_id = _meta_text(child_meta, spawn, "id")
        if not child_id or child_id not in linked_child_ids:
            raise ValueError("Codex child session_meta has an invalid child id")
        role = _meta_text(child_meta, spawn, "agent_role")
        evidence = _instruction_text(child_meta) + "\n" + _message_text(child_events)
        matches = tuple(
            child
            for child in requested.children
            if child.role == role
            and f"task_id: {child.task_id}" in evidence
            and f"router_plan_digest: {child.plan_digest}" in evidence
            and f"role_definition_digest: {child.definition_digest}" in evidence
        )
        if len(matches) != 1:
            raise ValueError("Codex child rollout does not uniquely match requested evidence")
        requested_child = matches[0]
        if requested_child.task_id in observed:
            raise ValueError("Codex child rollouts duplicate a requested task identity")
        child_contexts = _payloads(child_events, "turn_context")
        if not child_contexts:
            raise ValueError("Codex child rollout omitted turn_context")
        child_model = _unique_text(
            "child", "model", [context.get("model") for context in child_contexts], required=True
        )
        child_effort = _unique_text(
            "child", "effort", [context.get("effort") for context in child_contexts], required=True
        )
        child_cli_version = _meta_text(child_meta, spawn, "cli_version")
        if child_cli_version:
            child_cli_versions.add(child_cli_version)
        observed[requested_child.task_id] = replace(
            requested_child,
            effective_backend=AGENT_BACKEND_CODEX,
            effective_model=child_model,
            effective_effort=child_effort,
            session_id=child_id,
        )
    if set(observed) != {child.task_id for child in requested.children}:
        raise ValueError("Codex child rollouts omitted a requested task identity")
    if len(child_cli_versions) > 1:
        raise ValueError("Codex child rollouts have conflicting CLI versions")
    return replace(
        effective,
        cli_version=next(iter(child_cli_versions), cli_version),
        children=tuple(observed.values()),
    )
