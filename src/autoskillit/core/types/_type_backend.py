"""Backend capability declaration type. Zero autoskillit imports."""

from __future__ import annotations

import re as _re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ._type_checkpoint import SessionCheckpoint
from ._type_enums import BackendEventKind, HookTrustPolicy, OutputFormat
from ._type_native_shell_capture import (
    ManagedHeadlessSessionLineageRef,
    NativeShellCaptureDecision,
)
from ._type_plugin_source import PluginLaunchBinding, normalize_inherited_fds
from ._type_recipe_delivery import RecipeDeliveryBudgetDef
from ._type_results import ValidatedAddDir

__all__ = [
    "ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS",
    "BackendCapabilities",
    "BackendConventions",
    "CLAUDE_CODE_CAPABILITIES",
    "CLAUDE_MODEL_ALIASES",
    "CODEX_EFFORT_MAPPING",
    "CODEX_MODEL_ALIASES",
    "CODEX_MODEL_ALIASES_LAST_VERIFIED",
    "CODEX_VALID_MODEL_IDS",
    "SKILL_MODEL_CLASSES",
    "SKILL_REASONING_EFFORTS",
    "CmdOrigin",
    "CmdSpec",
    "CookSessionHandle",
    "ExecutableLaunchBinding",
    "ModelTranslation",
    "SessionSummary",
    "SkillSessionConfig",
    "ClaudeEventData",
    "CodexEventData",
    "SessionEvent",
    "AgentSessionResult",
    "is_valid_codex_model_id",
    "model_class",
    "strip_context_window_suffix",
]


@dataclass(frozen=True, slots=True)
class ExecutableLaunchBinding:
    """Canonical executable and sealed environment for one interactive launch."""

    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int
    file_sha256: str
    cwd: Path
    launch_environment: Mapping[str, str] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class BackendConventions:
    """Per-backend filesystem layout conventions for skill discovery.

    Distinct from :class:`BackendCapabilities` (which declares behavioral
    capability flags). Conventions describe directory layout: where the
    backend looks for skills, and which project-local directories to scan.
    """

    #: Relative path from backend session root to the skills directory.
    skills_subdir: Path = Path("skills")
    #: Project-relative directories to scan for project-local skills.
    project_local_skill_search_dirs: tuple[str, ...] = ()
    #: Persistent generated-home root below the configured project temp directory.
    persistent_session_root_subdir: Path | None = None
    #: Native model-facing skill invocation sigil.
    skill_sigil: str = "/"


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
    # Tool names whose calls are subject to write_guard enforcement for this backend
    write_guard_tool_names: frozenset[str] = field(default_factory=frozenset)
    # Env var prefixes stripped before session launch
    env_denylist_prefixes: tuple[str, ...] = field(default=())
    # Forward-declared: planned for version validation in doctor
    min_version: str = ""
    # Forward-declared: planned for version validation in doctor
    version_check_command: str = ""
    # Binary stem used for backend coherence check at session launch
    process_name: str = ""
    # All process names the backend binary may appear as in /proc/comm
    # or ps output (e.g., interpreter names for shebang scripts)
    process_name_aliases: frozenset[str] = field(default_factory=frozenset)
    # Relative path from session root to the skills directory
    skills_subdir: str = ""
    # Hook config format identifier (e.g. settings.json vs config.toml)
    hook_config_format: str = ""
    # Write detection strategy (e.g. tool_names, file_change)
    write_detection_strategy: str = ""
    # Patch format for write-guard path extraction (e.g. unified_diff)
    patch_format: str = ""
    # Default sandbox mode for skill sessions
    default_skill_sandbox_mode: str = ""
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
    # True when MCP tool names may use Claude marketplace registration state.
    # Backends without this capability always use the direct/runtime prefix.
    claude_marketplace_tool_prefix_capable: bool = field(default=False)
    # True when backend supports Health Inspector LLM-callback idle detection
    inspector_capable: bool = field(default=False)
    # True when backend CLI natively understands context-window suffixes like [1m]
    # and translate_model must preserve them in the --model flag value
    supports_context_window_suffix: bool = field(default=False)
    # Gates backend-specific prompt supplements that warn against reading raw package files
    has_unguarded_filesystem_access: bool = field(default=False)
    # True when backend's git metadata directories (.git/worktrees/) are writable
    # True when the backend can make outbound GitHub API write calls without sandbox restriction
    github_api_callable: bool = field(default=False)
    # Native skill invocation prefix character used by this backend's model/CLI.
    # Claude Code uses "/" (slash-commands via the Skill tool).
    # Codex uses "$" (dollar-mention via extract_tool_mentions).
    skill_sigil: str = "/"
    # Whether the backend requires persistent (non-ephemeral) session directories.
    # When True, session skill directories are placed under the project-relative
    # temp directory (via resolve_temp_dir) instead of volatile tmpfs (/dev/shm).
    # This is necessary when subagents inherit the session directory path as an
    # environment variable and may access it after the parent process exits.
    session_dir_persistent: bool = False
    # True when interactive cook launches support the guarded startup observer.
    cook_startup_observer_capable: bool = False
    # True when backend honors the disable-model-invocation SKILL.md frontmatter
    # key. When False, tier-2 skills are structurally omitted from the session
    # directory rather than written with gating frontmatter that the backend
    # would ignore.
    supports_model_invocation_gating: bool = True
    # Unnegotiated tool-result bound in tokens: the lowest operative bound
    # when a caller has not supplied protected host evidence for a larger
    # result. Distinct from history-retention configuration and negotiated
    # recipe delivery decisions.
    #
    # Default of 10,000 matches the smallest registered backend bound
    # (Codex code-mode). Any new backend that omits this field inherits
    # conservative bounding rather than the historical 0-sentinel "skip
    # bounding" behavior — preventing a silent opt-out where a future
    # backend without an explicit capability setting would be delivered
    # without any delivery-bound enforcement.
    unnegotiated_tool_result_token_limit: int = 10_000
    # True only when a protected host channel can attest the selected outer
    # result limit before nested MCP execution. Current backends remain False
    # until a version-pinned conformance report enables an evidence identity.
    protected_recipe_delivery_capable: bool = False
    # Backend-owned authority for ordinary and protected recipe delivery.
    # None means the backend has no version-pinned recipe-delivery contract;
    # protected delivery must then fail closed even if capability data drifts.
    recipe_delivery_budget: RecipeDeliveryBudgetDef | None = None
    # Interactive hook trust behavior. Automated builders retain their explicit
    # bypass policy; interactive launchers translate this policy into CLI flags.
    hook_trust_policy: HookTrustPolicy = HookTrustPolicy.AUTOMATED


ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS: tuple[str, ...] = (
    ".claude/skills",
    ".autoskillit/skills",
    ".codex/skills",
    ".agents/skills",
)


_CONTEXT_WINDOW_SUFFIX_RE: _re.Pattern[str] = _re.compile(r"\[\d+[mk]?\]$", _re.IGNORECASE)

CLAUDE_MODEL_ALIASES: dict[str, str] = {
    "sonnet": "sonnet",
    "opus": "opus",
    "haiku": "haiku",
}

CODEX_MODEL_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "sonnet": "gpt-5.6-sol",
        "opus": "gpt-5.6-sol",
        "haiku": "gpt-5.6-sol",
    }
)

CODEX_MODEL_ALIASES_LAST_VERIFIED: str = "2026-07-09"

CODEX_VALID_MODEL_IDS: frozenset[str] = frozenset({"gpt-5.5", "gpt-5.6-sol"})

assert set(CODEX_MODEL_ALIASES.values()).issubset(CODEX_VALID_MODEL_IDS), (
    "CODEX_MODEL_ALIASES values must all be members of CODEX_VALID_MODEL_IDS; "
    f"got {sorted(set(CODEX_MODEL_ALIASES.values()) - CODEX_VALID_MODEL_IDS)}"
)

CODEX_EFFORT_MAPPING: dict[str, str] = {
    "sonnet": "high",
    "opus": "xhigh",
    "haiku": "medium",
}

# Portable skill semantics use the existing canonical alias keys as model
# classes. Backend adapters remain the only authority that translates a class
# to a physical model ID and effort setting.
SKILL_MODEL_CLASSES: frozenset[str] = frozenset(CLAUDE_MODEL_ALIASES)
assert SKILL_MODEL_CLASSES == frozenset(CODEX_MODEL_ALIASES), (
    "backend model alias registries must expose the same semantic model classes"
)
SKILL_REASONING_EFFORTS: frozenset[str] = frozenset(CODEX_EFFORT_MAPPING.values())


def _codex_unique_model_reverse(aliases: Mapping[str, str]) -> Mapping[str, str]:
    """Return reverse aliases only for native model IDs used by one local class.

    Shared native IDs are intentionally omitted so model_class() falls back to
    the Codex model ID instead of projecting to an arbitrary local class.
    """
    values = tuple(aliases.values())
    return {model_id: alias for alias, model_id in aliases.items() if values.count(model_id) == 1}


# Reverse lookup is valid only for one-to-one native IDs. When multiple local
# classes share a Codex model, the class is carried by model_reasoning_effort.
_CODEX_MODEL_REVERSE: Mapping[str, str] = _codex_unique_model_reverse(CODEX_MODEL_ALIASES)


@dataclass(frozen=True, slots=True)
class ModelTranslation:
    """Bundled result of model alias translation for future protocol unification.

    Forward-declared to enable a future migration from translate_model() -> str
    plus model_config_overrides() -> tuple to a single translate_model() ->
    ModelTranslation protocol method.
    """

    model_id: str
    config_overrides: tuple[str, ...] = ()


def strip_context_window_suffix(model: str) -> str:
    return _CONTEXT_WINDOW_SUFFIX_RE.sub("", model)


def is_valid_codex_model_id(model_id: str) -> bool:
    return model_id in CODEX_VALID_MODEL_IDS


def model_class(model: str) -> str:
    base = strip_context_window_suffix(model)
    if base in CLAUDE_MODEL_ALIASES:
        return base
    return _CODEX_MODEL_REVERSE.get(base, base)


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
    supports_tool_list_changed=False,
    required_skill_fields=frozenset({"name", "description"}),
    required_session_files=frozenset(),
    session_dir_symlinks=frozenset(),
    applicable_guards=frozenset({"skill_load_guard"}),
    write_guard_tool_names=frozenset({"Write", "Edit", "Bash", "apply_patch"}),
    env_denylist_prefixes=(),
    min_version="2.1.142",
    version_check_command="claude --version",
    process_name="claude",
    process_name_aliases=frozenset({"claude"}),
    skills_subdir=".claude/skills",
    hook_config_format="",
    write_detection_strategy="tool_names",
    patch_format="unified_diff",
    default_skill_sandbox_mode="",
    mcp_env_forward_vars=frozenset(),
    replay_capable=True,
    record_capable=True,
    anthropic_provider_capable=True,
    plugin_install_capable=True,
    claude_marketplace_tool_prefix_capable=True,
    inspector_capable=False,
    supports_context_window_suffix=True,
    has_unguarded_filesystem_access=False,
    github_api_callable=True,
    skill_sigil="/",
    session_dir_persistent=False,
    cook_startup_observer_capable=False,
    supports_model_invocation_gating=True,
    unnegotiated_tool_result_token_limit=46_500,
    protected_recipe_delivery_capable=False,
    recipe_delivery_budget=None,
    hook_trust_policy=HookTrustPolicy.AUTOMATED,
)


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """Backend-neutral summary of a resumable coding-agent session."""

    backend_name: str
    session_id: str
    launch_id: str | None
    cwd: str
    first_prompt: str
    summary: str
    git_branch: str | None
    modified: str | None
    is_sidechain: bool
    session_type_hint: str | None


@dataclass(frozen=True, slots=True)
class CookSessionHandle:
    """Ownership handle for one durable interactive-cook attempt."""

    view_id: str
    pass_fds: tuple[int, ...]
    _record_spawn: Callable[[int, int], None] = field(repr=False, compare=False)
    _record_reaped: Callable[[int, int], None] = field(repr=False, compare=False)

    def record_spawn(self, pid: int, pgid: int) -> None:
        self._record_spawn(pid, pgid)

    def record_reaped(self, pid: int, pgid: int) -> None:
        self._record_reaped(pid, pgid)


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
    process_idle_timeout_ms: int = 0
    inherited_fds: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "inherited_fds",
            normalize_inherited_fds(self.inherited_fds),
        )


@dataclass(frozen=True, slots=True)
class SkillSessionConfig:
    """Configuration for a single skill session launch."""

    completion_marker: str = ""
    model: str | None = None
    plugin_binding: PluginLaunchBinding | None = None
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
    sandbox_mode: str = "workspace-write"
    network_access: bool = False
    native_shell_capture_decision: NativeShellCaptureDecision | None = None
    managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None
    managed_attempt_id: str | None = None

    def __post_init__(self) -> None:
        managed_values = (
            self.native_shell_capture_decision,
            self.managed_lineage_ref,
            self.managed_attempt_id,
        )
        if any(value is not None for value in managed_values) and not all(
            value is not None for value in managed_values
        ):
            raise ValueError("managed native shell capture fields must be supplied together")
        if self.managed_attempt_id is not None and not _re.fullmatch(
            r"[0-9a-f]{32}", self.managed_attempt_id
        ):
            raise ValueError("managed_attempt_id must be 32 lowercase hexadecimal characters")


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
