"""Backend capability declaration type. Zero autoskillit imports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ._type_enums import BackendEventKind

__all__ = [
    "BackendCapabilities",
    "CLAUDE_CODE_CAPABILITIES",
    "CmdSpec",
    "ClaudeEventData",
    "CodexEventData",
    "SessionEvent",
    "AgentSessionResult",
]


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    channel_b_capable: bool
    pty_required: bool
    session_resume_capable: bool
    skill_injection_capable: bool
    supports_thinking_blocks: bool
    supports_claude_format_stdout: bool
    exit_code_is_terminal: bool
    completion_record_types: frozenset[str]
    session_record_types: frozenset[str]


CLAUDE_CODE_CAPABILITIES: BackendCapabilities = BackendCapabilities(
    channel_b_capable=True,
    pty_required=True,
    session_resume_capable=True,
    skill_injection_capable=True,
    supports_thinking_blocks=True,
    supports_claude_format_stdout=True,
    exit_code_is_terminal=False,
    completion_record_types=frozenset({"result"}),
    session_record_types=frozenset({"assistant"}),
)


@dataclass(frozen=True, slots=True)
class CmdSpec:
    cmd: tuple[str, ...]
    env: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ClaudeEventData:
    """Event data from the Claude Code backend.

    Field naming follows Claude Code's JSONL schema: `session_id` identifies
    the Claude Code session and `subtype` carries the assistant-event subtype.
    These differ from CodexEventData (which uses `thread_id`/`item_type`)
    because each backend uses its own native terminology.
    Mutation of `raw` is prohibited by convention — the frozen constraint
    prevents field reassignment but not dict mutation.
    """

    record_type: str
    subtype: str
    session_id: str
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CodexEventData:
    """Event data from the Codex/OpenAI backend.

    Field naming follows the OpenAI Responses API schema: `thread_id`
    identifies the conversation thread and `item_type` carries the item kind.
    These differ from ClaudeEventData (which uses `session_id`/`subtype`)
    because each backend uses its own native terminology.
    See `ClaudeEventData` for the `raw`-field mutation convention.
    """

    record_type: str
    thread_id: str
    item_type: str
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SessionEvent:
    kind: BackendEventKind
    is_terminal: bool
    has_marker: bool
    session_id: str | None = None
    exit_code: int | None = None
    backend_data: ClaudeEventData | CodexEventData | None = None


@dataclass(frozen=True, slots=True)
class AgentSessionResult:
    success: bool
    exit_code: int
    backend_name: str
    elapsed_seconds: float
    session_id: str | None = None
    output: str = ""
    error: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)
