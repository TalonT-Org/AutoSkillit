"""Core StrEnum discriminators.

Zero autoskillit imports. Provides the shared enum vocabulary for all higher layers.
"""

from __future__ import annotations

from enum import StrEnum, unique
from typing import assert_never

__all__ = [
    "RetryReason",
    "MergeFailedStep",
    "MergeState",
    "RestartScope",
    "SkillExecutionRole",
    "SkillSource",
    "SkillInvalidityKind",
    "RemediationAction",
    "RecipeSource",
    "ClaudeFlags",
    "VARIADIC_CLAUDE_FLAGS",
    "NON_VARIADIC_CLAUDE_FLAGS",
    "OutputFormat",
    "Severity",
    "TerminationReason",
    "TerminationAction",
    "KillReason",
    "ChannelConfirmation",
    "SessionOutcome",
    "HookTrustPolicy",
    "ObserverStatus",
    "CliSubtype",
    "ChannelBStatus",
    "PRState",
    "SessionType",
    "session_type_for_skill_execution_role",
    "FleetErrorCode",
    "FeatureLifecycle",
    "IssueLabelState",
    "DispatchGateType",
    "ClaudeContentBlockType",
    "InfraExitCategory",
    "BackendEventKind",
    "CodexEventType",
    "CodexItemType",
    "SynthesisStrategy",
    "AdmissionState",
    "AdmissionDecisionKind",
    "ContextAdmissionAccountingStatus",
    "ContextAdmissionStorageHealthStatus",
    "ContextAdmissionStorageFailureReason",
    "ChargeDomain",
    "GenerationState",
    "MeasurementKind",
    "CoverageState",
    "CoverageEvidenceKind",
    "ReserveClass",
    "WitnessKind",
    "ProducerSurface",
]


class RetryReason(StrEnum):
    RESUME = "resume"
    STALE = "stale"  # Transient stale session — retry from scratch; not a context limit
    NONE = "none"
    BUDGET_EXHAUSTED = "budget_exhausted"
    EARLY_STOP = "early_stop"
    ZERO_WRITES = "zero_writes"
    EMPTY_OUTPUT = "empty_output"  # NATURAL_EXIT + rc=0 + no output; no write evidence at exit
    COMPLETED_NO_FLUSH = (
        "completed_no_flush"  # EMPTY_OUTPUT + write evidence; stdout absent (not merely unflushed)
    )
    DRAIN_RACE = "drain_race"  # channel-confirmed completion, stdout not fully flushed before kill
    PATH_CONTAMINATION = "path_contamination"  # CWD boundary violation, not a context limit
    CONTRACT_RECOVERY = (
        "contract_recovery"  # marker present + write evidence — omission not structural
    )
    CLONE_CONTAMINATION = "clone_contamination"
    THINKING_STALL = "thinking_stall"  # final turn: thinking blocks only, no text or tool output
    IDLE_STALL = "idle_stall"  # stdout idle watchdog kill — session may have partial progress
    RATE_LIMITED = "rate_limited"  # transient HTTP 429 or rate-limit pattern — wait-and-retry
    CANCELLED = "cancelled"
    OUTCOME_INVARIANT = (
        "outcome_invariant"  # skill-emitted outcome fields violated a contract invariant
    )


class InfraExitCategory(StrEnum):
    """Infrastructure-level exit classification for headless sessions.

    Distinguishes infrastructure failures (context exhaustion, API errors,
    process kills) from logical agent failures (completed with error).
    Used for telemetry and resume routing — not a retry discriminant itself.
    """

    COMPLETED = "completed"
    CONTEXT_EXHAUSTED = "context_exhausted"
    API_ERROR = "api_error"
    PROCESS_KILLED = "process_killed"
    RATE_LIMITED = "rate_limited"


class MergeFailedStep(StrEnum):
    PATH_VALIDATION = "path_validation"
    PROTECTED_BRANCH = "protected_branch"
    BRANCH_DETECTION = "branch_detection"
    DIRTY_TREE = "dirty_tree"
    DIRTY_MAIN_REPO = "dirty_main_repo"
    TEST_GATE = "test_gate"
    FETCH = "fetch"
    PRE_REBASE_CHECK = "pre_rebase_check"
    MERGE_COMMITS_DETECTED = "merge_commits_detected"
    REBASE = "rebase"
    GENERATED_FILE_CLEANUP = "generated_file_cleanup"
    POST_REBASE_TEST_GATE = "post_rebase_test_gate"
    MERGE = "merge"
    EDITABLE_INSTALL_GUARD = "editable_install_guard"
    EMBEDDED_WORKTREE = "embedded_worktree"
    REF_COHERENCE = "ref_coherence"


class MergeState(StrEnum):
    WORKTREE_INTACT = "worktree_intact"
    WORKTREE_INTACT_REBASE_ABORTED = "worktree_intact_rebase_aborted"
    WORKTREE_INTACT_BASE_NOT_PUBLISHED = "worktree_intact_base_not_published"
    WORKTREE_INTACT_MERGE_COMMITS_DETECTED = "worktree_intact_merge_commits_detected"
    WORKTREE_INTACT_REF_DIVERGED = "worktree_intact_ref_diverged"
    WORKTREE_DIRTY = "worktree_dirty"
    WORKTREE_DIRTY_ABORT_FAILED = "worktree_dirty_abort_failed"
    WORKTREE_DIRTY_MID_OPERATION = "worktree_dirty_mid_operation"
    MAIN_REPO_MERGE_ABORTED = "main_repo_merge_aborted"
    MAIN_REPO_DIRTY_ABORT_FAILED = "main_repo_dirty_abort_failed"
    MERGE_SUCCEEDED_CLEANUP_BLOCKED = "merge_succeeded_cleanup_blocked"


class RestartScope(StrEnum):
    FULL_RESTART = "full_restart"
    PARTIAL_RESTART = "partial_restart"


class SkillExecutionRole(StrEnum):
    """Exact orchestration role authorized to execute a skill contract."""

    SESSION = "session"
    ORCHESTRATOR = "orchestrator"
    FLEET = "fleet"


class SkillSource(StrEnum):
    BUNDLED = "bundled"
    BUNDLED_EXTENDED = "bundled_extended"
    PROJECT_LOCAL = "project_local"
    THIRD_PARTY = "third_party"


class SkillInvalidityKind(StrEnum):
    """One enumerable reason a skill contract failed validation.

    Mechanically enumerable so a forcing-function registry
    (``SKILL_CONTRACT_REMEDIATIONS``) can require every kind to declare how
    pre-existing artifacts are handled before a new validation may ship.
    """

    FRONTMATTER_PARSE = "frontmatter_parse"
    FIELD_SHAPE = "field_shape"
    EXPLORATION_CONTRACT_INVALID = "exploration_contract_invalid"
    RESERVED_FIELD = "reserved_field"
    UNKNOWN_CAPABILITY = "unknown_capability"
    UNDECLARED_CAPABILITY = "undeclared_capability"
    SEMANTIC_UNDECLARED_TOKENS = "semantic_undeclared_tokens"
    SEMANTIC_MISSING_VERSION = "semantic_missing_version"
    SEMANTIC_VERSION_MISMATCH = "semantic_version_mismatch"
    SEMANTIC_PLAN_INVALID = "semantic_plan_invalid"


class RemediationAction(StrEnum):
    """Whether a SkillInvalidityKind can be auto-repaired or only advised on."""

    DETERMINISTIC = "deterministic"
    ADVISORY = "advisory"


class RecipeSource(StrEnum):
    PROJECT = "project"
    BUILTIN = "builtin"


class ClaudeFlags(StrEnum):
    """Canonical registry of all claude CLI flags used by autoskillit.

    Every flag string that autoskillit passes to the claude binary MUST be
    defined here. Call sites must reference these constants — never hardcode
    flag strings at the call site.

    When the claude CLI renames or removes a flag:
      1. Update the constant value here.
      2. Follow the failing tests in test_flag_contracts.py to update call sites.
    """

    # Permission bypass
    ALLOW_DANGEROUSLY_SKIP_PERMISSIONS = (
        "--allow-dangerously-skip-permissions"  # enables option without activating
    )
    DANGEROUSLY_SKIP_PERMISSIONS = "--dangerously-skip-permissions"  # actually bypasses all checks

    # Prompt / execution mode
    PRINT = "-p"

    # Model selection
    MODEL = "--model"

    # Plugin / directory
    PLUGIN_DIR = "--plugin-dir"
    ADD_DIR = "--add-dir"

    # Output format
    OUTPUT_FORMAT = "--output-format"
    VERBOSE = "--verbose"

    # Session resume
    RESUME = "--resume"

    # Interactive session restrictions
    TOOLS = "--tools"
    APPEND_SYSTEM_PROMPT = "--append-system-prompt"


VARIADIC_CLAUDE_FLAGS: frozenset[str] = frozenset({ClaudeFlags.ADD_DIR, ClaudeFlags.TOOLS})

NON_VARIADIC_CLAUDE_FLAGS: frozenset[str] = frozenset(
    {
        ClaudeFlags.ALLOW_DANGEROUSLY_SKIP_PERMISSIONS,
        ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS,
        ClaudeFlags.PRINT,
        ClaudeFlags.MODEL,
        ClaudeFlags.PLUGIN_DIR,
        ClaudeFlags.OUTPUT_FORMAT,
        ClaudeFlags.VERBOSE,
        ClaudeFlags.RESUME,
        ClaudeFlags.APPEND_SYSTEM_PROMPT,
    }
)


class OutputFormat(StrEnum):
    """Claude CLI output format with declared data capabilities.

    STREAM_JSON emits per-turn NDJSON records (type=assistant, type=result),
    providing assistant_messages and model_breakdown.
    JSON emits a single result envelope — no assistant records.
    """

    JSON = "json"
    STREAM_JSON = "stream-json"

    @property
    def supports_assistant_messages(self) -> bool:
        return self == OutputFormat.STREAM_JSON

    @property
    def supports_model_breakdown(self) -> bool:
        return self == OutputFormat.STREAM_JSON

    @property
    def required_cli_flags(self) -> tuple[str, ...]:
        """CLI flags required when this format is used with -p (headless) mode."""
        if self == OutputFormat.STREAM_JSON:
            return (ClaudeFlags.VERBOSE,)
        return ()

    @classmethod
    def derive(cls, *, completion_marker: str) -> OutputFormat:
        """Derive the required format from feature configuration.

        If completion_marker is set, recovery requires assistant_messages,
        which requires STREAM_JSON format.
        """
        if completion_marker:
            return cls.STREAM_JSON
        return cls.JSON


class Severity(StrEnum):
    OK = "ok"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class TerminationReason(StrEnum):
    """How a managed subprocess ended.

    Propagates termination provenance from run_managed_async to consumers,
    replacing implicit inference from exit codes.
    """

    NATURAL_EXIT = "natural_exit"
    COMPLETED = "completed"
    STALE = "stale"
    IDLE_STALL = "idle_stall"
    TIMED_OUT = "timed_out"
    SIGNAL_DEATH = "signal_death"
    HEALTH_INSPECTOR = "health_inspector"


class TerminationAction(StrEnum):
    """What execute_termination_action should do with a subprocess after the race loop.

    Produced by decide_termination_action (pure function) and consumed by
    execute_termination_action (the single authorized kill caller in process.py).

    - NO_KILL: process already exited naturally; no kill needed.
    - DRAIN_THEN_KILL_IF_ALIVE: channel confirmed completion but process is still
      alive; wait up to grace_seconds for natural exit, then kill if still running.
    - IMMEDIATE_KILL: timeout, stall, or stale — kill without draining.
    """

    NO_KILL = "no_kill"
    DRAIN_THEN_KILL_IF_ALIVE = "drain_then_kill_if_alive"
    IMMEDIATE_KILL = "immediate_kill"


class KillReason(StrEnum):
    """Why the subprocess was (or was not) killed.

    Carried by SubprocessResult and SkillResult so the formatter can annotate
    the exit_code line and resolve the cognitive contradiction
    "success=True + exit_code=-9".
    """

    NATURAL_EXIT = "natural_exit"
    KILL_AFTER_COMPLETION = "kill_after_completion"  # drain window expired
    INFRA_KILL = "infra_kill"  # timeout / stall / stale
    EXCEPTION = "exception"  # runner raised an unhandled exception
    NOT_APPLICABLE = "not_applicable"  # no subprocess ran (gate/headless error)
    HEALTH_INSPECTOR = "health_inspector"  # inspector callback issued KILL verdict


class ChannelConfirmation(StrEnum):
    """How subprocess completion was confirmed by the two-channel detection system.

    Replaces SubprocessResult.data_confirmed: bool to eliminate ambiguity
    between "Channel A confirmed content" and "no monitoring ran".

    Invariant (from process.py):
    - CHANNEL_A: heartbeat fired; stdout contains non-empty type=result record.
    - CHANNEL_B: session JSONL marker fired; drain expired OR no heartbeat configured.
      stdout may be empty. Downstream must not require stdout content.
    - UNMONITORED: no channel monitoring active (NATURAL_EXIT, STALE, TIMED_OUT,
      sync path, or heartbeat disabled with no Channel B win).
    - DIR_MISSING: session log directory did not exist when monitoring started.
      Monitoring was structurally impossible. Distinct from UNMONITORED (which
      means monitoring ran but produced no confirmation).
    """

    CHANNEL_A = "channel_a"
    CHANNEL_B = "channel_b"
    UNMONITORED = "unmonitored"
    DIR_MISSING = "dir_missing"


class SessionOutcome(StrEnum):
    """Classification of a completed headless session.

    Maps bijectively from the two-field (success, needs_retry) boolean pair
    on SkillResult to a single named discriminant:

        SUCCEEDED  → (success=True,  needs_retry=False)
        RETRIABLE  → (success=False, needs_retry=True)
        FAILED     → (success=False, needs_retry=False)

    The combination (success=True, needs_retry=True) is structurally impossible
    and has no corresponding member.
    """

    SUCCEEDED = "succeeded"
    RETRIABLE = "retriable"
    FAILED = "failed"


class HookTrustPolicy(StrEnum):
    """Interactive hook trust behavior for a coding-agent backend."""

    AUTOMATED = "automated"
    REVIEW_EACH_SESSION = "review_each_session"


class ObserverStatus(StrEnum):
    """Typed outcomes from a guarded startup-readiness adapter."""

    READY = "ready"
    ABSENT = "absent"
    LOCKED = "locked"
    CORRUPT = "corrupt"
    INCOMPLETE = "incomplete"
    SCHEMA_CHANGED = "schema_changed"
    UNSUPPORTED_VERSION = "unsupported_version"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class CliSubtype(StrEnum):
    """Sealed enum for Claude CLI session subtypes.

    Every subtype value emitted by the Claude CLI or synthesized internally
    MUST be a member of this enum. The from_cli() constructor maps unknown
    CLI strings to UNKNOWN instead of raising ValueError, because the Claude
    CLI may introduce new subtype strings in future versions.
    """

    SUCCESS = "success"
    ERROR_MAX_TURNS = "error_max_turns"
    ERROR_DURING_EXECUTION = "error_during_execution"
    CONTEXT_EXHAUSTION = "context_exhaustion"
    UNKNOWN = "unknown"
    EMPTY_OUTPUT = "empty_output"
    UNPARSEABLE = "unparseable"
    TIMEOUT = "timeout"
    INTERRUPTED = "interrupted"
    IDLE_STALL = "idle_stall"

    @classmethod
    def from_cli(cls, raw: str) -> CliSubtype:
        """Convert a raw CLI subtype string to a CliSubtype member.

        Unknown strings map to UNKNOWN instead of raising ValueError.
        """
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


class ChannelBStatus(StrEnum):
    """Sealed enum for Channel B monitor status values.

    Replaces the raw string ``"completion"`` / ``"stale"`` convention with
    compile-time exhaustiveness enforcement via assert_never.

    - COMPLETION: session JSONL marker was found; monitoring succeeded.
    - STALE: monitoring ran but timed out with no marker found.
    - DIR_MISSING: session log directory did not exist when monitoring started.
      Monitoring was structurally impossible. Distinct from STALE (which
      means monitoring ran but produced no marker).
    """

    COMPLETION = "completion"
    STALE = "stale"
    DIR_MISSING = "dir_missing"


class PRState(StrEnum):
    """Terminal state of a PR as classified by the merge queue watcher.

    Each member is returned only when a positive signal confirms the state.
    EJECTED requires either state=CLOSED or mergeable=CONFLICTING.
    DROPPED_HEALTHY means auto_merge was cleared on an otherwise healthy PR.
    """

    MERGED = "merged"
    EJECTED = "ejected"
    EJECTED_CI_FAILURE = "ejected_ci_failure"
    STALLED = "stalled"
    DROPPED_HEALTHY = "dropped_healthy"
    DROPPED_MERGE_GROUP_CI = "dropped_merge_group_ci"
    NOT_ENROLLED = "not_enrolled"
    TIMEOUT = "timeout"
    ERROR = "error"


class SessionType(StrEnum):
    """Orchestration level discriminator for the session hierarchy.

    Each level can only call the level directly below it:

        L3 (FLEET) -> L2 (ORCHESTRATOR) -> L1 (headless worker) -> L0 (subagent)

    FLEET        -- L3: top-level campaign dispatcher.
                    Launches L2 food trucks via dispatch_food_truck.
    ORCHESTRATOR -- L2: recipe runner (interactive via order, or headless food truck).
                    Launches L1 headless workers via run_skill.
    SKILL        -- L1 skill session (headless worker launched by an orchestrator via
                    run_skill). L0 subagents are the actual leaf nodes — they never
                    set AUTOSKILLIT_SESSION_TYPE and cannot call run_skill.

    Note: interactive L1 sessions (autoskillit cook, bare Claude Code) have no
    SessionType value -- they bypass tier checks because AUTOSKILLIT_HEADLESS is unset.
    """

    FLEET = "fleet"
    ORCHESTRATOR = "orchestrator"
    SKILL = "skill"


def session_type_for_skill_execution_role(role: SkillExecutionRole) -> SessionType:
    """Map a machine-contract role to its runtime session discriminator."""
    match role:
        case SkillExecutionRole.SESSION:
            return SessionType.SKILL
        case SkillExecutionRole.ORCHESTRATOR:
            return SessionType.ORCHESTRATOR
        case SkillExecutionRole.FLEET:
            return SessionType.FLEET
        case _ as unreachable:
            assert_never(unreachable)


@unique
class FleetErrorCode(StrEnum):
    """Registered error codes for fleet dispatch failures.

    Every fleet error envelope must use one of these codes.
    Unregistered codes are rejected by fleet_error() at runtime.
    """

    FLEET_PARALLEL_REFUSED = "fleet_parallel_refused"
    FLEET_UNKNOWN_INGREDIENT = "fleet_unknown_ingredient"
    FLEET_MISSING_INGREDIENT = "fleet_missing_ingredient"
    FLEET_CAMPAIGN_HALTED = "fleet_campaign_halted"
    FLEET_RECIPE_NOT_FOUND = "fleet_recipe_not_found"
    FLEET_INVALID_RECIPE_KIND = "fleet_invalid_recipe_kind"
    FLEET_HARD_REFUSAL_HEADLESS = "fleet_hard_refusal_headless"
    FLEET_FEATURE_DISABLED = "fleet_feature_disabled"
    FLEET_MANIFEST_MISSING = "fleet_manifest_missing"
    FLEET_MANIFEST_CORRUPTED = "fleet_manifest_corrupted"
    FLEET_LOCK_NOT_INITIALIZED = "fleet_lock_not_initialized"
    FLEET_L3_TIMEOUT = "fleet_l3_timeout"
    FLEET_L3_NO_RESULT_BLOCK = "fleet_l3_no_result_block"
    FLEET_L3_PARSE_FAILED = "fleet_l3_parse_failed"
    FLEET_L3_STARTUP_OR_CRASH = "fleet_l3_startup_or_crash"
    FLEET_BUDGET_EXCEEDED = "fleet_budget_exceeded"
    FLEET_QUOTA_EXHAUSTED = "fleet_quota_exhausted"
    FLEET_CLEANUP_FAILED = "fleet_cleanup_failed"
    FLEET_GATE_UNKNOWN_DISPATCH = "fleet_gate_unknown_dispatch"
    FLEET_GATE_ALREADY_RECORDED = "fleet_gate_already_recorded"
    FLEET_GATE_NO_CAMPAIGN = "fleet_gate_no_campaign"
    FLEET_ACQUIRE_TIMEOUT = "fleet_acquire_timeout"
    FLEET_RECIPE_INVALID = "fleet_recipe_invalid"
    FLEET_PROCESS_STALE = "fleet_process_stale"
    FLEET_DISPATCH_SKIPPED = "fleet_dispatch_skipped"
    FLEET_RESUME_SESSION_MISSING = "fleet_resume_session_missing"
    FLEET_RESET_NOT_FOUND = "fleet_reset_not_found"
    FLEET_RESET_INVALID_TARGET = "fleet_reset_invalid_target"
    FLEET_RESET_STILL_RUNNING = "fleet_reset_still_running"
    FLEET_INVALID_BACKEND = "fleet_invalid_backend"


class FeatureLifecycle(StrEnum):
    """Lifecycle stage of a registered feature gate.

    EXPERIMENTAL — On by default when experimental_enabled=True; disabled on main/stable.
    STABLE       — On by default everywhere; opt-out via config.
    DEPRECATED   — Scheduled for removal; follows default_enabled.
    DISABLED     — Never enabled; config cannot override. For in-progress/unsafe features.
    """

    EXPERIMENTAL = "experimental"
    STABLE = "stable"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class IssueLabelState(StrEnum):
    """Lifecycle state of a GitHub issue managed by the label state machine."""

    QUEUED = "queued"
    IN_PROGRESS = "in-progress"
    STAGED = "staged"
    FAIL = "fail"


@unique
class DispatchGateType(StrEnum):
    """Valid gate types for campaign dispatch entries."""

    CONFIRM = "confirm"


class ClaudeContentBlockType(StrEnum):
    """Sealed enum for Claude API content block types.

    Every block type that can appear in an assistant message content array
    MUST be a member of this enum. The from_api() constructor maps unknown
    block types to UNKNOWN instead of raising ValueError, because the Claude
    API may introduce new block types in future versions.

    Exhaustive match dispatch over this enum (with assert_never on the
    fallthrough arm) provides compile-time enforcement that all block types
    are handled — adding a new member forces the developer to handle it.
    """

    TEXT = "text"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    REDACTED_THINKING = "redacted_thinking"
    IMAGE = "image"
    UNKNOWN = "unknown"

    @classmethod
    def from_api(cls, raw: str) -> ClaudeContentBlockType:
        """Convert a raw API block type string to a ClaudeContentBlockType member.

        Unknown strings map to UNKNOWN instead of raising ValueError.
        """
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


class BackendEventKind(StrEnum):
    """Event kinds emitted by coding agent backends."""

    COMPLETION = "completion"
    SESSION_META = "session_meta"
    API_RETRY = "api_retry"
    TOOL_OUTPUT = "tool_output"
    TASK_LIFECYCLE = "task_lifecycle"
    SCHEDULE_WAKEUP = "schedule_wakeup"
    ERROR = "error"
    IGNORED = "ignored"


class CodexEventType(StrEnum):
    """Sealed enum for Codex CLI NDJSON top-level event types.

    The from_ndjson() constructor maps unknown event types to UNKNOWN
    instead of raising ValueError.
    """

    THREAD_STARTED = "thread.started"
    SESSION_META = "session_meta"
    TURN_STARTED = "turn.started"
    ITEM_STARTED = "item.started"
    ITEM_UPDATED = "item.updated"
    ITEM_COMPLETED = "item.completed"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    ERROR = "error"
    UNKNOWN = "unknown"

    @classmethod
    def from_ndjson(cls, raw: str) -> CodexEventType:
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


class CodexItemType(StrEnum):
    """Sealed enum for Codex CLI item.completed item types.

    The from_ndjson() constructor maps unknown item types to UNKNOWN
    instead of raising ValueError.
    """

    AGENT_MESSAGE = "agent_message"
    COMMAND_EXECUTION = "command_execution"
    FILE_CHANGE = "file_change"
    MCP_TOOL_CALL = "mcp_tool_call"
    COLLAB_TOOL_CALL = "collab_tool_call"
    WEB_SEARCH = "web_search"
    MESSAGE = "message"
    FUNCTION_CALL = "function_call"
    REASONING = "reasoning"
    TODO_LIST = "todo_list"
    UNKNOWN = "unknown"

    @classmethod
    def from_ndjson(cls, raw: str) -> CodexItemType:
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


class SynthesisStrategy(StrEnum):
    """Recognized synthesis strategies for phoropter output aggregation."""

    NULL = "null"
    PRIORITY_HIERARCHY = "priority_hierarchy"
    ELECTRE_III = "electre_iii"
    DEX = "dex"
    CUSTOM = "custom"


@unique
class AdmissionState(StrEnum):
    """Lifecycle states for one immutable context-admission occurrence."""

    PROPOSED = "proposed"
    RESERVED = "reserved"
    PREPARED = "prepared"
    HISTORY_STAGED = "history_staged"
    REQUEST_DISPATCHED = "request_dispatched"
    COMMITTED = "committed"
    RELEASED = "released"
    ROLLED_BACK = "rolled_back"
    INVALIDATED = "invalidated"
    INDETERMINATE = "indeterminate"
    QUARANTINED = "quarantined"


@unique
class AdmissionDecisionKind(StrEnum):
    """Closed decision vocabulary returned by the protocol-v1 reducer."""

    WOULD_ADMIT = "would_admit"
    WOULD_REJECT = "would_reject"
    WATERMARK_UNAVAILABLE = "watermark_unavailable"
    UPSTREAM_GATED = "upstream_gated"
    NOOP_IDEMPOTENT = "noop_idempotent"
    CONFLICT = "conflict"
    IDEMPOTENCY_EXPIRED = "idempotency_expired"
    QUARANTINED = "quarantined"


@unique
class ContextAdmissionAccountingStatus(StrEnum):
    """Closed outcome vocabulary for durable context accounting."""

    RECORDED = "recorded"
    EXACT_REPLAY = "exact_replay"
    SEMANTIC_REJECTION = "semantic_rejection"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    PROTOCOL_QUARANTINED = "protocol_quarantined"
    CONTENDED = "contended"
    STORAGE_FAIL_CLOSED = "storage_fail_closed"


@unique
class ContextAdmissionStorageHealthStatus(StrEnum):
    """Storage health, intentionally separate from protocol lifecycle."""

    UNINITIALIZED = "uninitialized"
    HEALTHY = "healthy"
    FAIL_CLOSED = "fail_closed"


@unique
class ContextAdmissionStorageFailureReason(StrEnum):
    """Bounded reasons for sticky storage-health failure."""

    CONFIGURATION = "configuration"
    IO = "io"
    SECURITY_IDENTITY = "security_identity"
    INTEGRITY = "integrity"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    UNSUPPORTED_ENCODING = "unsupported_encoding"
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    REPLAY_MISMATCH = "replay_mismatch"
    IDENTITY_MISMATCH = "identity_mismatch"
    AMBIGUOUS_RECOVERY = "ambiguous_recovery"


@unique
class ChargeDomain(StrEnum):
    """Capacity domains kept separate by the admission contract."""

    INPUT_CONTEXT = "input_context"
    OUTPUT_GENERATION = "output_generation"


@unique
class GenerationState(StrEnum):
    """Lifecycle of a generated-output allowance."""

    RESERVED = "reserved"
    STREAMING = "streaming"
    RECONCILED = "reconciled"
    INDETERMINATE = "indeterminate"
    QUARANTINED = "quarantined"


@unique
class MeasurementKind(StrEnum):
    """Authority level of a count supplied to the pure reducer."""

    PROVIDER_EXACT = "provider_exact"
    TOKENIZER_EXACT = "tokenizer_exact"
    HOST_ESTIMATE = "host_estimate"
    BYTE_EMERGENCY = "byte_emergency"


@unique
class CoverageState(StrEnum):
    """Evidence-backed observation or authority coverage state."""

    VERIFIED = "verified"
    PARTIAL = "partial"
    UPSTREAM_GATED = "upstream_gated"


@unique
class CoverageEvidenceKind(StrEnum):
    """Primary and inference evidence kinds accepted by the coverage registry."""

    AUTOSKILLIT_SOURCE = "autoskillit_source"
    CODEX_SOURCE = "codex_source"
    CODEX_OFFICIAL_DOC = "codex_official_doc"
    CODEX_RUNTIME_PROBE = "codex_runtime_probe"
    INFERENCE = "inference"


@unique
class ReserveClass(StrEnum):
    """Capability-scoped context reserve classes."""

    ORDINARY = "ordinary"
    SYNTHESIS = "synthesis"
    FINAL_RESPONSE = "final_response"


@unique
class WitnessKind(StrEnum):
    """Closed vocabulary of authoritative admission witnesses."""

    EPOCH_SNAPSHOT = "epoch_snapshot"
    INPUT_COUNTED = "input_counted"
    HISTORY_STAGED = "history_staged"
    REPRESENTATION_BOUND = "representation_bound"
    REQUEST_INCLUDED = "request_included"
    PROVIDER_ACCEPTED = "provider_accepted"
    OUTPUT_USAGE = "output_usage"
    TRUNCATION = "truncation"
    NON_ADMISSION = "non_admission"
    ROLLBACK = "rollback"
    RECONCILIATION = "reconciliation"
    IDEMPOTENCY_EXPIRY = "idempotency_expiry"
    EPOCH_FENCE = "epoch_fence"
    EPOCH_ROLLOVER = "epoch_rollover"


@unique
class ProducerSurface(StrEnum):
    """Every model-visible producer covered by protocol version 1."""

    NATIVE_SHELL = "native_shell"
    UNIFIED_EXEC_AND_WRITE_STDIN = "unified_exec_and_write_stdin"
    APPLY_PATCH = "apply_patch"
    AUTOSKILLIT_MCP = "autoskillit_mcp"
    EXTERNAL_MCP = "external_mcp"
    AUTOSKILLIT_LOCAL_FUNCTION = "autoskillit_local_function"
    OTHER_LOCAL_FUNCTION = "other_local_function"
    MCP_RESOURCE = "mcp_resource"
    CLIENT_PROVIDER_RETRIEVAL = "client_provider_retrieval"
    CODE_MODE_AGGREGATE = "code_mode_aggregate"
    HOSTED_SPECIALIZED_TOOL = "hosted_specialized_tool"
    HOOK_FEEDBACK = "hook_feedback"
    TOOL_ARGUMENT = "tool_argument"
    TOOL_RESULT_ENVELOPE = "tool_result_envelope"
    USER_PROMPT = "user_prompt"
    ASSISTANT_OUTPUT_HISTORY = "assistant_output_history"
    SKILL_PLUGIN_CONTEXT = "skill_plugin_context"
    OTHER_CONTEXT_INJECTION = "other_context_injection"
    HEADLESS_CHILD_PROMPT = "headless_child_prompt"
    PARENT_VISIBLE_CHILD_DELIVERY = "parent_visible_child_delivery"
    COMPACTION_MODEL_WINDOW_TRANSITION = "compaction_model_window_transition"
