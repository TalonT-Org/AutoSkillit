"""Backend capability declaration type. Zero autoskillit imports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ._type_checkpoint import SessionCheckpoint
from ._type_enums import BackendEventKind, OutputFormat
from ._type_plugin_source import PluginSource
from ._type_results import ValidatedAddDir

__all__ = [
    "BackendCapabilities",
    "CLAUDE_CODE_CAPABILITIES",
    "CmdSpec",
    "SkillSessionConfig",
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
    mcp_config_capable: bool
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
    mcp_config_capable=False,
    completion_record_types=frozenset({"result"}),
    session_record_types=frozenset({"assistant"}),
)


@dataclass(frozen=True, slots=True)
class CmdSpec:
    cmd: tuple[str, ...]
    env: Mapping[str, str]
    cwd: str = ""


@dataclass(frozen=True, slots=True)
class SkillSessionConfig:
    completion_marker: str = ""
    model: str | None = None
    plugin_source: PluginSource | None = None
    output_format: OutputFormat = OutputFormat.JSON
    add_dirs: tuple[ValidatedAddDir, ...] = ()
    exit_after_stop_delay_ms: int = 0
    stream_idle_timeout_ms: int = 0
    scenario_step_name: str = ""
    temp_dir_relpath: str | None = None
    allowed_write_prefix: str = ""
    provider_extras: Mapping[str, str] | None = None
    profile_name: str = ""
    resume_session_id: str = ""
    resume_checkpoint: SessionCheckpoint | None = None
    resume_message: str | None = None


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
    usage: Mapping[str, Any] | None = None
    file_changes: tuple[Mapping[str, Any], ...] | None = None
    command: str | None = None


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
