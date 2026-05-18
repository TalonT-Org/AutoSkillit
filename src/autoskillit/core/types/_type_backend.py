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
    cmd: list[str]
    env: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ClaudeEventData:
    record_type: str
    subtype: str
    session_id: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CodexEventData:
    record_type: str
    thread_id: str
    item_type: str
    raw: dict[str, Any] = field(default_factory=dict)


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
    session_id: str
    backend_name: str
    elapsed_seconds: float
    output: str = ""
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
