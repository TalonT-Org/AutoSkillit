#!/usr/bin/env python3
"""SubagentStart/SubagentStop/SessionEnd and parent Agent-Task lifecycle observer.

Records every observed L0 child run in the durable child-outcome snapshot
(``_child_outcome_snapshot``) at the delegation boundary (issue #4623).
Purely observational (``mechanism="side-effect"``): never denies, never
mutates tool behavior, always exits 0. Registered for Claude's native
``SubagentStart``/``SubagentStop``/``SessionEnd`` lifecycle events, the
shared ``PostToolUse``/``PostToolUseFailure`` surface for
``Agent``/``Task``/``spawn_agent`` tool calls (covering both backends), and
Codex's ``Stop`` event (session-end equivalent — Codex has no ``SessionEnd``).

Correlation limitation (documented, not fabricated — see the issue #4623
Step 1 investigation notes, gitignored under the project temp directory):
Claude hook payloads
carry no parent-agent field, and a ``PostToolUse`` payload for an
``Agent``/``Task`` tool call does not reliably carry the spawned child's
``agent_id`` (only ``SubagentStart``/``SubagentStop`` do, per the official
hooks documentation). This hook keys ``PostToolUse``/``PostToolUseFailure``
evidence by the parent-side ``tool_use_id`` — a genuine, structural
identifier, not a guessed correlation — and leaves alias reconciliation with
the ``SubagentStart``/``SubagentStop``-observed ``agent_id`` row to the
execution-layer collector (``execution/child_outcomes.py``), which can read
the actual child transcript.

For native Codex ``spawn_agent``-family tool calls, this hook intentionally
writes nothing to the snapshot: a pre-spawn reservation is never proof of
child creation (per the Step 2 design decision), and confirming the real
child thread requires structural rollout evidence
(``sub_agent_activity``/``agent_thread_id``) that only the execution-layer
collector parses — duplicating that parser here would both violate this
module's stdlib-only boundary and duplicate existing logic.

Stdlib-only — no autoskillit imports.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)

# Resolved dynamically (matching hooks/_runtime/_hook_settings.py:read_session_binding's
# precedent) rather than a literal ``from _child_outcome_snapshot import (...)``,
# which static analysis cannot resolve ahead of the sys.path bootstrap above.
_snapshot_module = importlib.import_module("_child_outcome_snapshot")
HARNESS_API_ERROR_LITERAL = getattr(_snapshot_module, "HARNESS_API_ERROR_LITERAL")
finalize_snapshot_at_session_end = getattr(_snapshot_module, "finalize_snapshot_at_session_end")
observe_child = getattr(_snapshot_module, "observe_child")
record_terminal_evidence = getattr(_snapshot_module, "record_terminal_evidence")
resolve_child_outcome_log_root = getattr(_snapshot_module, "resolve_child_outcome_log_root")
resolve_snapshot_path = getattr(_snapshot_module, "resolve_snapshot_path")

#: Tool names that spawn or address a child across both backends: Claude's
#: Agent/Task tools, Codex's spawn_agent tool.
_CHILD_SPAWN_TOOL_NAMES = frozenset({"Agent", "Task", "spawn_agent"})

#: Bound on how much result text is retained as raw evidence (avoid storing
#: an entire child transcript in the snapshot).
_RESULT_TEXT_EVIDENCE_CAP = 2048


def _current_backend() -> str:
    return (
        "codex"
        if os.environ.get("AUTOSKILLIT_AGENT_BACKEND", "").strip() == "codex"
        else ("claude_code")
    )


def _snapshot_path_for(backend: str, parent_session_id: str) -> Path | None:
    if not parent_session_id:
        return None
    log_root = resolve_child_outcome_log_root(caller="child_outcome_hook")
    if log_root is None:
        return None
    try:
        return resolve_snapshot_path(
            log_root, backend=backend, parent_session_id=parent_session_id
        )
    except Exception:
        return None


def _handle_subagent_start(data: dict) -> None:
    parent_session_id = data.get("session_id")
    agent_id = data.get("agent_id")
    if not isinstance(parent_session_id, str) or not parent_session_id:
        return
    if not isinstance(agent_id, str) or not agent_id:
        return
    snapshot_path = _snapshot_path_for("claude_code", parent_session_id)
    if snapshot_path is None:
        return
    observe_child(
        snapshot_path,
        backend="claude_code",
        parent_session_id=parent_session_id,
        child_id=agent_id,
    )
    agent_type = data.get("agent_type")
    if isinstance(agent_type, str) and agent_type:
        record_terminal_evidence(
            snapshot_path,
            backend="claude_code",
            parent_session_id=parent_session_id,
            child_id=agent_id,
            evidence_key=f"claude_code:{agent_id}:subagent_start:role",
            evidence={"role": agent_type, "evidence_source": "subagent_start"},
        )


def _handle_subagent_stop(data: dict) -> None:
    parent_session_id = data.get("session_id")
    agent_id = data.get("agent_id")
    if not isinstance(parent_session_id, str) or not parent_session_id:
        return
    if not isinstance(agent_id, str) or not agent_id:
        return
    snapshot_path = _snapshot_path_for("claude_code", parent_session_id)
    if snapshot_path is None:
        return
    reason = data.get("reason")
    transcript = data.get("agent_transcript_path")
    # A generic SubagentStop reason — including the documented example
    # "completed" — is not promoted to a confirmed terminal reason: the
    # official docs enumerate no value set, and Step 1 could not pin a
    # live-captured distinction between "the turn loop stopped" and "the
    # child's task actually completed". The raw value is preserved for
    # diagnosis; classification stays unknown per classify_evidence.
    evidence: dict[str, object] = {"evidence_source": "subagent_stop"}
    if isinstance(reason, str):
        evidence["subagent_stop_reason"] = reason
    if isinstance(transcript, str) and transcript:
        evidence["transcript_locator"] = transcript
    reason_key = reason if isinstance(reason, str) else ""
    record_terminal_evidence(
        snapshot_path,
        backend="claude_code",
        parent_session_id=parent_session_id,
        child_id=agent_id,
        evidence_key=f"claude_code:{agent_id}:subagent_stop:{reason_key}",
        evidence=evidence,
    )


def _extract_result_text(data: dict) -> str:
    tool_response = data.get("tool_response")
    if isinstance(tool_response, str):
        return tool_response
    if isinstance(tool_response, dict):
        for key in ("result", "output", "text", "content"):
            value = tool_response.get(key)
            if isinstance(value, str):
                return value
    for key in ("reason", "error"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def _handle_agent_task_result(data: dict, *, event_type: str) -> None:
    tool_name = data.get("tool_name")
    if tool_name not in _CHILD_SPAWN_TOOL_NAMES:
        return
    if tool_name == "spawn_agent":
        # See module docstring: native Codex child confirmation is deferred
        # to the execution-layer rollout collector.
        return
    parent_session_id = data.get("session_id")
    tool_use_id = data.get("tool_use_id")
    if not isinstance(parent_session_id, str) or not parent_session_id:
        return
    if not isinstance(tool_use_id, str) or not tool_use_id:
        return
    snapshot_path = _snapshot_path_for("claude_code", parent_session_id)
    if snapshot_path is None:
        return

    result_text = _extract_result_text(data)
    evidence: dict[str, object] = {"evidence_source": event_type.lower()}
    if HARNESS_API_ERROR_LITERAL in result_text:
        evidence["harness_literal"] = result_text[:_RESULT_TEXT_EVIDENCE_CAP]

    record_terminal_evidence(
        snapshot_path,
        backend="claude_code",
        parent_session_id=parent_session_id,
        child_id=tool_use_id,
        evidence_key=f"claude_code:{tool_use_id}:{event_type}:result",
        evidence=evidence,
    )


def _handle_session_end(data: dict) -> None:
    parent_session_id = data.get("session_id")
    if not isinstance(parent_session_id, str) or not parent_session_id:
        return
    snapshot_path = _snapshot_path_for("claude_code", parent_session_id)
    if snapshot_path is None:
        return
    finalize_snapshot_at_session_end(
        snapshot_path, backend="claude_code", parent_session_id=parent_session_id
    )


def _handle_codex_stop(data: dict) -> None:
    parent_session_id = data.get("session_id")
    if not isinstance(parent_session_id, str) or not parent_session_id:
        return
    snapshot_path = _snapshot_path_for("codex", parent_session_id)
    if snapshot_path is None:
        return
    finalize_snapshot_at_session_end(
        snapshot_path, backend="codex", parent_session_id=parent_session_id
    )


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    event_type = data.get("hook_event_name")
    if not isinstance(event_type, str):
        sys.exit(0)

    try:
        if event_type == "SubagentStart":
            _handle_subagent_start(data)
        elif event_type == "SubagentStop":
            _handle_subagent_stop(data)
        elif event_type in ("PostToolUse", "PostToolUseFailure"):
            _handle_agent_task_result(data, event_type=event_type)
        elif event_type == "SessionEnd":
            _handle_session_end(data)
        elif event_type == "Stop" and _current_backend() == "codex":
            _handle_codex_stop(data)
    except Exception as exc:
        # Purely observational: never fail the parent's turn over a
        # diagnostic-sink error.
        sys.stderr.write(f"child_outcome_hook: observation failed: {exc}\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
