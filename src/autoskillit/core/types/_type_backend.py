"""Backend capability declaration type. Zero autoskillit imports."""

from __future__ import annotations

import re as _re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ._type_checkpoint import SessionCheckpoint
from ._type_enums import BackendEventKind, OutputFormat
from ._type_plugin_source import PluginSource
from ._type_results import ValidatedAddDir

__all__ = [
    "BackendCapabilities",
    "BackendConventions",
    "CLAUDE_CODE_CAPABILITIES",
    "CLAUDE_MODEL_ALIASES",
    "CODEX_MODEL_ALIASES",
    "CmdOrigin",
    "CmdSpec",
    "SkillSessionConfig",
    "ClaudeEventData",
    "CodexEventData",
    "SessionEvent",
    "AgentSessionResult",
    "strip_context_window_suffix",
]


@dataclass(frozen=True, slots=True)
class BackendConventions:
    """Per-backend filesystem layout conventions for skill discovery.

    Distinct from :class:`BackendCapabilities` (which declares behavioral
    capability flags). Conventions describe directory layout: where the
    backend looks for skills, and which project-local directories to scan.
    """

    #: Project-relative directories to scan for project-local skills.
    project_local_skill_search_dirs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    """Per-backend capability declaration consumed by runtime gates.

    Every field must have at least one production read site in src/ —
    enforced by tests/arch/test_capability_consumption.py. Fields without
    a consumer must be added to the _FORWARD_DECLARED exemption set as a
    ForwardDeclaredField(issue=NNNN, rationale="...", added_date=date(...))
    entry with a linked tracking issue.
    """

    # True when backend streams a side-channel JSONL log (Channel B)
    channel_b_capable: bool = field(default=False)
    # True when the subprocess needs a pseudo-TTY allocation
    pty_required: bool = field(default=False)
    # True when backend supports --resume <session_id>
    session_resume_capable: bool = field(default=False)
    # True when backend accepts --add-dir / --plugin-dir skill injection
    skill_injection_capable: bool = field(default=False)
    # Forward-declared: planned for thinking-block rendering
    supports_thinking_blocks: bool = field(default=False)
    # True when backend stdout is Claude JSON format
    supports_claude_format_stdout: bool = field(default=False)
    # True when non-zero exit code definitively signals failure
    exit_code_is_terminal: bool = field(default=False)
    # Forward-declared: planned for MCP config wiring
    mcp_config_capable: bool = field(default=False)
    # True when backend can be used for food-truck (fleet) dispatches
    food_truck_capable: bool = field(default=False)
    # JSONL record types that signal session completion
    completion_record_types: frozenset[str] = field(default_factory=frozenset)
    # JSONL record types that constitute session activity
    session_record_types: frozenset[str] = field(default_factory=frozenset)
    # True when backend supports LLM triage via claude -p
    triage_capable: bool = field(default=False)
    # Forward-declared: planned for context exhaustion handling
    supports_context_exhaustion_detection: bool = field(default=False)
    # True when backend supports project-local --add-dir skill discovery
    project_local_skills_capable: bool = field(default=False)
    # False triggers pre-reveal kitchen at startup instead of notification-driven reveal
    supports_tool_list_changed: bool = field(default=True)
    # SKILL.md front-matter fields required by this backend
    required_skill_fields: frozenset[str] = field(default_factory=frozenset)
    # Files that must be copied into the session directory at launch
    required_session_files: frozenset[str] = field(default_factory=frozenset)
    # Symlink targets to create in the session directory at launch
    session_dir_symlinks: frozenset[str] = field(default_factory=frozenset)
    # Guard script names that apply to sessions for this backend
    applicable_guards: frozenset[str] = field(default_factory=frozenset)
    # Env var prefixes stripped before session launch
    env_denylist_prefixes: tuple[str, ...] = field(default=())
    # Forward-declared: planned for version validation in doctor
    min_version: str = ""
    # Forward-declared: planned for version validation in doctor
    version_check_command: str = ""
    # Binary stem used for backend coherence check at session launch
    process_name: str = ""
    # Relative path from session root to the skills directory
    skills_subdir: str = ""
    # Env vars that must appear in CmdSpec.env for all cmd-builders (MCP forwarding)
    mcp_env_forward_vars: frozenset[str] = field(default_factory=frozenset)
    # True when backend supports api_simulator-based REPLAY_SCENARIO runner wrapping
    replay_capable: bool = field(default=False)
    # True when backend supports api_simulator-based RECORD_SCENARIO runner wrapping
    record_capable: bool = field(default=False)
    # True when backend is the Anthropic provider (Claude Code) — used to gate
    # provider-override routing in run_skill() on capability rather than backend name.
    anthropic_provider_capable: bool = field(default=False)
    # True when backend supports Claude plugin install/list CLI
    plugin_install_capable: bool = field(default=False)
    # True when backend supports Health Inspector LLM-callback idle detection
    inspector_capable: bool = field(default=False)


_CONTEXT_WINDOW_SUFFIX_RE: _re.Pattern[str] = _re.compile(r"\[\d+[mk]?\]$", _re.IGNORECASE)

CLAUDE_MODEL_ALIASES: dict[str, str] = {
    "sonnet": "sonnet",
    "opus": "opus",
    "haiku": "haiku",
}

CODEX_MODEL_ALIASES: dict[str, str] = {
    "sonnet": "o4-mini",
    "opus": "o3",
    "haiku": "gpt-4o-mini",
}


def strip_context_window_suffix(model: str) -> str:
    return _CONTEXT_WINDOW_SUFFIX_RE.sub("", model)


CLAUDE_CODE_CAPABILITIES: BackendCapabilities = BackendCapabilities(
    channel_b_capable=True,
    pty_required=True,
    session_resume_capable=True,
    skill_injection_capable=True,
    supports_thinking_blocks=True,
    supports_claude_format_stdout=True,
    exit_code_is_terminal=False,
    mcp_config_capable=False,
    food_truck_capable=True,
    completion_record_types=frozenset({"result"}),
    session_record_types=frozenset({"assistant"}),
    triage_capable=True,
    supports_context_exhaustion_detection=True,
    project_local_skills_capable=True,
    supports_tool_list_changed=True,
    required_skill_fields=frozenset({"name", "description"}),
    required_session_files=frozenset(),
    session_dir_symlinks=frozenset(),
    applicable_guards=frozenset({"skill_load_guard"}),
    env_denylist_prefixes=(),
    min_version="",
    version_check_command="claude --version",
    process_name="claude",
    skills_subdir=".claude/skills",
    replay_capable=True,
    record_capable=True,
    anthropic_provider_capable=True,
    plugin_install_capable=True,
    inspector_capable=True,
)


@dataclass(frozen=True, slots=True)
class CmdOrigin:
    """Provenance metadata for a CmdSpec, capturing the structural role of each element."""

    binary: str
    mode_flags: tuple[str, ...] = ()
    kv_flags: tuple[tuple[str, str], ...] = ()
    positional: tuple[str, ...] = ()
    variadic_pairs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class CmdSpec:
    """Fully-resolved subprocess command specification passed to the runner."""

    cmd: tuple[str, ...]
    env: Mapping[str, str]
    cwd: str = ""
    origin: CmdOrigin | None = None
    is_resume: bool = False


@dataclass(frozen=True, slots=True)
class SkillSessionConfig:
    """Configuration for a single skill session launch."""

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
    allowed_write_prefixes: tuple[str, ...] = ()
    provider_extras: Mapping[str, str] | None = None
    profile_name: str = ""
    resume_session_id: str = ""
    resume_checkpoint: SessionCheckpoint | None = None
    resume_message: str | None = None
    backend_override: str | None = None


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
    """A single parsed event emitted by a running backend session."""

    kind: BackendEventKind
    is_terminal: bool
    has_marker: bool
    session_id: str | None = None
    exit_code: int | None = None
    backend_data: ClaudeEventData | CodexEventData | None = None


@dataclass(frozen=True, slots=True)
class AgentSessionResult:
    """Final result produced by a completed agent session."""

    success: bool
    exit_code: int
    backend_name: str
    elapsed_seconds: float
    session_id: str | None = None
    output: str = ""
    error: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)
