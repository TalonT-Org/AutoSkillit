"""Channel A lifecycle / candidate normalization for the Claude backend.

Pure, side-effect-free helpers used by ``ClaudeStreamParser`` to translate
one parsed Claude NDJSON record into its typed lifecycle observations and
its (optional) parent-assistant marker. This module deliberately holds no
mutable state — every parser owns its own bucket of imports and the
coordinator owns its reducer.

The translation rules:

- ``system`` records with ``subtype in {task_started, task_progress}``
  yield an ACTIVE observation carrying every non-blank native alias.
- ``system`` records with ``subtype == task_notification`` yield a
  terminal observation whose state matches the notification's
  ``status`` field. Records carrying a ``replaces`` edge advance the
  attempt generation.
- ``user`` records containing ``tool_result`` blocks with structured
  async evidence (``async_launched`` / ``isAsync``) yield an ACTIVE
  observation. ``status in {completed, failed, cancelled}`` yields a
  terminal observation. Missing status and unknown task kinds fail
  closed (no observation is emitted).
- ``assistant`` records whose payload contains the completion marker
  yield a ``ParentAssistantMarker`` carrying the record's native UUID,
  message ID, byte offset, and backend session ID. Markers with
  blank or non-string UUIDs fail closed and never become candidates.

Task kind is never inferred from ``toolu_`` prefixes, prose, or process
metadata — the canonical ``Agent`` / ``Bash`` distinction is preserved
on every observation so the coordinator can refuse cross-kind matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autoskillit.core import (
    ChildAttemptState,
    ChildLifecycleObservation,
    LifecycleEvidenceIssue,
    LifecycleEvidenceIssueKind,
    ParentAssistantMarker,
)
from autoskillit.execution.session._session_model import _is_parent_assistant_record

__all__ = [
    "LifecycleEvidenceIssue",
    "ParentAssistantCandidate",
    "extract_lifecycle_observations",
    "extract_parent_assistant_marker",
    "extract_lifecycle_issues",
]


@dataclass(frozen=True, slots=True)
class ParentAssistantCandidate:
    """Result of one parent-assistant marker extraction.

    A non-empty ``marker`` means the assistant record carried the
    completion marker; ``observation`` carries the typed child-lifecycle
    contribution so callers do not have to reparse the same record.
    """

    marker: ParentAssistantMarker | None
    observation: ChildLifecycleObservation | None = None


def _coerce_str(value: Any) -> str:
    """Coerce a JSONL field to a non-empty native string.

    Blank strings, non-strings, and absent values are normalized to
    ``""`` so the coordinator can fail-closed on aliases rather than
    stringifying a malformed payload (e.g., a numeric UUID).
    """
    if isinstance(value, str):
        return value
    return ""


def _task_kind_from_system(obj: dict[str, Any]) -> str:
    """Map a Claude ``system`` task record to its task_kind.

    The decision is purely structural: an ``agent_id`` field denotes
    Agent; a ``background_task_id`` field denotes Bash. The parser
    refuses to infer kind from prose, ``toolu_`` prefixes, or process
    metadata so a Bash notification cannot close an Agent obligation.
    """
    if obj.get("agent_id"):
        return "Agent"
    if obj.get("background_task_id"):
        return "Bash"
    return ""


def _state_from_notification(obj: dict[str, Any]) -> ChildAttemptState:
    """Map a Claude ``task_notification`` ``status`` field to a typed state."""
    status = obj.get("status")
    if status == "completed":
        return ChildAttemptState.COMPLETED
    if status == "failed":
        return ChildAttemptState.FAILED
    if status == "cancelled":
        return ChildAttemptState.CANCELLED
    if status == "timed_out":
        return ChildAttemptState.TIMED_OUT
    return ChildAttemptState.ACTIVE


def extract_lifecycle_observations(
    obj: dict[str, Any],
    record_type: str,
    *,
    byte_offset: int = 0,
) -> tuple[ChildLifecycleObservation, ...]:
    """Translate one Channel A record into its immutable lifecycle observations.

    Records handled:
    - ``system`` with ``subtype in {task_started, task_progress}`` — ACTIVE
    - ``system`` with ``subtype == task_notification`` — terminal (per status)
    - ``user`` tool_result carrying structured async_launched/isAsync — ACTIVE
    - ``user`` tool_result carrying status in {completed, failed, cancelled} — terminal

    Malformed or unknown records yield no observation; the caller can
    fail-closed by surfacing an empty tuple.
    """
    if record_type == "system":
        subtype = obj.get("subtype", "")
        if subtype not in {"task_started", "task_progress", "task_notification"}:
            return ()
        task_kind = _task_kind_from_system(obj)
        if not task_kind:
            return ()
        if subtype == "task_notification":
            state = _state_from_notification(obj)
        else:
            state = ChildAttemptState.ACTIVE
        replaces = _coerce_str(obj.get("replaces"))
        replaced_by = _coerce_str(obj.get("replaced_by"))
        return (
            ChildLifecycleObservation(
                task_kind=task_kind,
                task_id=_coerce_str(obj.get("task_id")),
                tool_use_id=_coerce_str(obj.get("tool_use_id")),
                agent_id=_coerce_str(obj.get("agent_id")),
                background_task_id=_coerce_str(obj.get("background_task_id")),
                attempt_state=state,
                source_event_id=_coerce_str(obj.get("uuid")),
                parent_turn_id=_coerce_str(obj.get("parent_message_id")),
                byte_offset=byte_offset,
                is_parent_declaration=subtype in {"task_started"},
                replaces_native_uuid=replaces,
                replaced_by_native_uuid=replaced_by,
            ),
        )

    if record_type == "user":
        message = obj.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return ()
        observations: list[ChildLifecycleObservation] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            tool_use_id = _coerce_str(block.get("tool_use_id"))
            tool_use_result = block.get("content")
            content_obj = tool_use_result if isinstance(tool_use_result, dict) else {}
            if not tool_use_result:
                continue
            is_async = bool(
                content_obj.get("async_launched") is True or content_obj.get("isAsync") is True
            )
            status = content_obj.get("status")
            if not is_async and status not in {"completed", "failed", "cancelled"}:
                continue
            if is_async:
                state = ChildAttemptState.ACTIVE
            elif status == "completed":
                state = ChildAttemptState.COMPLETED
            elif status == "failed":
                state = ChildAttemptState.FAILED
            elif status == "cancelled":
                state = ChildAttemptState.CANCELLED
            else:
                continue
            if content_obj.get("agentId"):
                task_kind = "Agent"
            elif content_obj.get("backgroundTaskId"):
                task_kind = "Bash"
            else:
                # A tool_use_id prefix is not native task-kind evidence.
                continue
            observations.append(
                ChildLifecycleObservation(
                    task_kind=task_kind,
                    tool_use_id=tool_use_id,
                    agent_id=_coerce_str(content_obj.get("agentId")),
                    background_task_id=_coerce_str(content_obj.get("backgroundTaskId")),
                    attempt_state=state,
                    source_event_id=_coerce_str(obj.get("uuid")),
                    parent_turn_id=_coerce_str(message.get("id"))
                    if isinstance(message, dict)
                    else "",
                    byte_offset=byte_offset,
                    is_user_result=True,
                )
            )
        return tuple(observations)

    return ()


def extract_parent_assistant_marker(
    obj: dict[str, Any],
    *,
    byte_offset: int = 0,
    completion_marker: str = "",
) -> ParentAssistantCandidate:
    """Extract a parent-assistant marker from one ``assistant`` record.

    A marker is emitted only when:

    - the record type is ``assistant``;
    - ``completion_marker`` is non-empty and the record's text content
      carries that marker as a standalone token;
    - the record's native ``uuid`` is a non-blank string (numeric,
      list, dict, and missing UUIDs fail closed);
    - the record's native ``message.id`` is present (corroboration only).

    No fallback synthesis: blank UUIDs, marker text, channel, session
    ID, fingerprint, or ``"unknown"`` cannot bridge to a candidate.
    """
    if not _is_parent_assistant_record(obj):
        return ParentAssistantCandidate(marker=None)
    raw_uuid = obj.get("uuid")
    if not isinstance(raw_uuid, str) or not raw_uuid:
        return ParentAssistantCandidate(marker=None)
    native_uuid = raw_uuid
    if native_uuid.lower() == "unknown" or native_uuid.isdigit():
        return ParentAssistantCandidate(marker=None)
    message = obj.get("message")
    message_id = _coerce_str(message.get("id")) if isinstance(message, dict) else ""
    if not message_id:
        return ParentAssistantCandidate(marker=None)
    if not completion_marker:
        return ParentAssistantCandidate(marker=None)
    if not _record_carries_marker_text(obj, completion_marker):
        return ParentAssistantCandidate(marker=None)
    session_id = _coerce_str(obj.get("session_id"))
    marker = ParentAssistantMarker(
        native_uuid=native_uuid,
        message_id=message_id,
        byte_offset=byte_offset,
        backend_session_id=session_id,
    )
    return ParentAssistantCandidate(marker=marker)


def _record_carries_marker_text(obj: dict[str, Any], completion_marker: str) -> bool:
    """Return True when one assistant record's text content carries the marker.

    Marker matching accepts only standalone unquoted marker text: the marker
    must appear as its own line in the text block, not embedded inside
    another word or quoted inside a string. Embedded/quoted markers
    return False so they cannot accidentally authorize completion.
    """
    if not completion_marker:
        return False
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text", "")
        if not isinstance(text, str) or not text:
            continue
        for line in text.splitlines():
            stripped = line.strip().strip("\"'`")
            if stripped == completion_marker:
                return True
    return False


def _canonical_fingerprint(task_kind: str, aliases: tuple[str, ...], source_uuid: str) -> str:
    """Derive the canonical fingerprint used for issue resolution.

    Two lifecycle observations referring to the same canonical child/event
    pair produce the same fingerprint; the coordinator uses exact-match
    against later valid evidence to clear blocking issues.
    """
    return f"{task_kind}|{'|'.join(aliases)}|{source_uuid}"


def extract_lifecycle_issues(
    obj: dict[str, Any],
    record_type: str,
    *,
    byte_offset: int = 0,
) -> tuple[LifecycleEvidenceIssue, ...]:
    """Emit typed blocking-evidence issues for malformed/unknown records.

    Fail-closed paths surface here instead of being silently swallowed:

    - Unknown ``status`` on a ``task_notification`` record -> ``UNKNOWN_STATUS``.
    - Records carrying ``tool_result`` blocks with both ``async_launched``
      and a terminal ``status`` (mixed launch + delivery on one record) ->
      ``MIXED_LAUNCH_AND_TERMINAL``.
    - Records whose status is recognised but whose identity fields are
      missing or malformed (e.g., terminal status without an agentId or
      backgroundTaskId) -> ``MALFORMED_IDENTITY``.

    Each issue carries a canonical fingerprint so the coordinator can match
    later valid evidence against it; unrelated evidence never clears an
    issue, and unresolved issues fail closed through the actor.
    """
    if record_type == "system":
        subtype = obj.get("subtype", "")
        if subtype != "task_notification":
            return ()
        status = obj.get("status")
        if status in {"completed", "failed", "cancelled", "timed_out"}:
            return ()
        source_uuid = _coerce_str(obj.get("uuid"))
        if not source_uuid:
            return ()
        task_kind = _task_kind_from_system(obj)
        aliases = (
            _coerce_str(obj.get("task_id")),
            _coerce_str(obj.get("tool_use_id")),
            _coerce_str(obj.get("agent_id")),
            _coerce_str(obj.get("background_task_id")),
        )
        return (
            LifecycleEvidenceIssue(
                issue_kind=LifecycleEvidenceIssueKind.UNKNOWN_STATUS,
                task_kind=task_kind or "unknown",
                native_aliases=aliases,
                source_event_uuid=source_uuid,
                canonical_fingerprint=_canonical_fingerprint(
                    task_kind or "unknown", aliases, source_uuid
                ),
                channel_relative_byte_offset=byte_offset,
                detail=f"unknown task_notification status: {status!r}",
            ),
        )

    if record_type == "user":
        message = obj.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return ()
        issues: list[LifecycleEvidenceIssue] = []
        source_uuid = _coerce_str(obj.get("uuid"))
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            tool_use_result = block.get("content")
            if not isinstance(tool_use_result, dict):
                continue
            is_async = bool(
                tool_use_result.get("async_launched") is True
                or tool_use_result.get("isAsync") is True
            )
            status = tool_use_result.get("status")
            if is_async and status in {"completed", "failed", "cancelled"}:
                tool_use_id = _coerce_str(block.get("tool_use_id"))
                agent_id = _coerce_str(tool_use_result.get("agentId"))
                background_task_id = _coerce_str(tool_use_result.get("backgroundTaskId"))
                if agent_id:
                    task_kind = "Agent"
                elif background_task_id:
                    task_kind = "Bash"
                else:
                    task_kind = "unknown"
                aliases_mixed: tuple[str, ...] = (tool_use_id, agent_id, background_task_id)
                fingerprint = _canonical_fingerprint(
                    task_kind, aliases_mixed, source_uuid or "anonymous"
                )
                issues.append(
                    LifecycleEvidenceIssue(
                        issue_kind=LifecycleEvidenceIssueKind.MIXED_LAUNCH_AND_TERMINAL,
                        task_kind=task_kind,
                        native_aliases=aliases_mixed,
                        source_event_uuid=source_uuid,
                        canonical_fingerprint=fingerprint,
                        channel_relative_byte_offset=byte_offset,
                        detail="async_launched=true delivered with terminal status",
                    )
                )
                continue
            if status in {"completed", "failed", "cancelled"}:
                tool_use_id = _coerce_str(block.get("tool_use_id"))
                agent_id = _coerce_str(tool_use_result.get("agentId"))
                background_task_id = _coerce_str(tool_use_result.get("backgroundTaskId"))
                if not (agent_id or background_task_id):
                    aliases_inner: tuple[str, ...] = (tool_use_id,)
                    task_kind = "unknown"
                    fingerprint = _canonical_fingerprint(
                        task_kind, aliases_inner, source_uuid or "anonymous"
                    )
                    issues.append(
                        LifecycleEvidenceIssue(
                            issue_kind=LifecycleEvidenceIssueKind.MALFORMED_IDENTITY,
                            task_kind=task_kind,
                            native_aliases=aliases_inner,
                            source_event_uuid=source_uuid,
                            canonical_fingerprint=fingerprint,
                            channel_relative_byte_offset=byte_offset,
                            detail="terminal tool_result missing native identity",
                        )
                    )
        return tuple(issues)

    return ()
