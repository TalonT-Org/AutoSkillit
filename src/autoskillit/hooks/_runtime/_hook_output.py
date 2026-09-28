"""Sole writer of hook protocol output, enforced by the output-authority arch test.

Channels render the shapes documented by Claude Code and Codex. Blocking uses
exit 2 with a reason on stderr, the blocking contract shared by both backends.
This module is stdlib-only so standalone hooks can import it by bare name.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from types import MappingProxyType
from typing import NamedTuple, NoReturn


class HookEmission(NamedTuple):
    stdout: str
    stderr: str
    exit_code: int


CHANNEL_EVENTS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "deny": frozenset({"PreToolUse"}),
        "block": frozenset(
            {"PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "SubagentStop"}
        ),
        "context": frozenset({"SessionStart", "PreToolUse", "PostToolUse", "UserPromptExpansion"}),
        "rewrite_input": frozenset({"PreToolUse"}),
        "rewrite_mcp_output": frozenset({"PostToolUse"}),
        "notify": frozenset({"Stop", "PostToolUse"}),
        "halt": frozenset({"PreCompact"}),
    }
)

EMITTER_CHANNELS: Mapping[str, str] = MappingProxyType(
    {
        "deny_tool_use": "deny",
        "block": "block",
        "add_context": "context",
        "allow_with_updated_input": "rewrite_input",
        "rewrite_mcp_tool_output": "rewrite_mcp_output",
        "notify": "notify",
        "halt_session": "halt",
    }
)


def _require_text(text: str) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Hook output text must be a non-empty string")


def _require_event(channel: str, event: str) -> None:
    if event not in CHANNEL_EVENTS[channel]:
        raise ValueError(f"Hook output channel {channel!r} does not support event {event!r}")


def _json_output(value: Mapping[str, object]) -> HookEmission:
    return HookEmission(json.dumps(value) + "\n", "", 0)


def render_deny(reason: str) -> HookEmission:
    _require_text(reason)
    return _json_output(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )


def render_block(reason: str) -> HookEmission:
    _require_text(reason)
    return HookEmission("", reason.rstrip("\n") + "\n", 2)


def render_context(event: str, text: str) -> HookEmission:
    _require_event("context", event)
    _require_text(text)
    return _json_output(
        {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
    )


def render_allow_with_updated_input(
    updated_input: Mapping[str, object], *, context: str | None = None
) -> HookEmission:
    if not isinstance(updated_input, Mapping) or not updated_input:
        raise ValueError("Updated hook input must be a non-empty mapping")
    output: dict[str, object] = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "updatedInput": dict(updated_input),
    }
    if context is not None:
        _require_text(context)
        output["additionalContext"] = context
    return _json_output({"hookSpecificOutput": output})


def render_mcp_tool_output(value: object) -> HookEmission:
    if isinstance(value, str):
        _require_text(value)
    return _json_output(
        {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedMCPToolOutput": value}}
    )


def render_notify(event: str, system_message: str, *, context: str | None = None) -> HookEmission:
    _require_event("notify", event)
    _require_text(system_message)
    output: dict[str, object] = {"systemMessage": system_message}
    if context is not None:
        if event != "PostToolUse":
            raise ValueError(f"Notification context is unsupported for event {event!r}")
        _require_text(context)
        output["hookSpecificOutput"] = {"hookEventName": event, "additionalContext": context}
    return _json_output(output)


def render_halt(stop_reason: str, *, system_message: str) -> HookEmission:
    _require_text(stop_reason)
    _require_text(system_message)
    return _json_output(
        {"continue": False, "stopReason": stop_reason, "systemMessage": system_message}
    )


def emit(emission: HookEmission) -> NoReturn:
    if emission.stdout:
        sys.stdout.write(emission.stdout)
        sys.stdout.flush()
    if emission.stderr:
        sys.stderr.write(emission.stderr)
        sys.stderr.flush()
    raise SystemExit(emission.exit_code)


def deny_tool_use(reason: str) -> NoReturn:
    emit(render_deny(reason))


def block(reason: str) -> NoReturn:
    emit(render_block(reason))


def add_context(event: str, text: str) -> NoReturn:
    emit(render_context(event, text))


def allow_with_updated_input(
    updated_input: Mapping[str, object], *, context: str | None = None
) -> NoReturn:
    emit(render_allow_with_updated_input(updated_input, context=context))


def rewrite_mcp_tool_output(value: object) -> NoReturn:
    emit(render_mcp_tool_output(value))


def notify(event: str, system_message: str, *, context: str | None = None) -> NoReturn:
    emit(render_notify(event, system_message, context=context))


def halt_session(stop_reason: str, *, system_message: str) -> NoReturn:
    emit(render_halt(stop_reason, system_message=system_message))
