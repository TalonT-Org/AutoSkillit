"""Core StrEnum discriminators.

Zero autoskillit imports. Provides the shared enum vocabulary for all higher layers.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "RetryReason",
    "MergeFailedStep",
    "MergeState",
    "RestartScope",
    "SkillSource",
    "RecipeSource",
    "ClaudeFlags",
    "OutputFormat",
    "Severity",
    "TerminationReason",
    "ChannelConfirmation",
    "SessionOutcome",
    "CliSubtype",
    "ChannelBStatus",
]


class RetryReason(StrEnum):
    RESUME = "resume"
    STALE = "stale"  # Transient stale session — retry from scratch; not a context limit
    NONE = "none"
    BUDGET_EXHAUSTED = "budget_exhausted"
    EARLY_STOP = "early_stop"
    ZERO_WRITES = "zero_writes"
    EMPTY_OUTPUT = "empty_output"  # NATURAL_EXIT + rc=0 + no output, no partial progress
    DRAIN_RACE = "drain_race"  # channel-confirmed completion, stdout not fully flushed before kill
    PATH_CONTAMINATION = "path_contamination"  # CWD boundary violation, not a context limit
    CONTRACT_RECOVERY = (
        "contract_recovery"  # marker present + write evidence — omission not structural
    )


class MergeFailedStep(StrEnum):
    PATH_VALIDATION = "path_validation"
    PROTECTED_BRANCH = "protected_branch"
    BRANCH_DETECTION = "branch_detection"
    DIRTY_TREE = "dirty_tree"
    TEST_GATE = "test_gate"
    FETCH = "fetch"
    PRE_REBASE_CHECK = "pre_rebase_check"
    MERGE_COMMITS_DETECTED = "merge_commits_detected"
    REBASE = "rebase"
    GENERATED_FILE_CLEANUP = "generated_file_cleanup"
    POST_REBASE_TEST_GATE = "post_rebase_test_gate"
    MERGE = "merge"


class MergeState(StrEnum):
    WORKTREE_INTACT = "worktree_intact"
    WORKTREE_INTACT_REBASE_ABORTED = "worktree_intact_rebase_aborted"
    WORKTREE_INTACT_BASE_NOT_PUBLISHED = "worktree_intact_base_not_published"
    WORKTREE_INTACT_MERGE_COMMITS_DETECTED = "worktree_intact_merge_commits_detected"
    WORKTREE_DIRTY = "worktree_dirty"
    WORKTREE_DIRTY_ABORT_FAILED = "worktree_dirty_abort_failed"
    WORKTREE_DIRTY_MID_OPERATION = "worktree_dirty_mid_operation"
    MAIN_REPO_MERGE_ABORTED = "main_repo_merge_aborted"
    MAIN_REPO_DIRTY_ABORT_FAILED = "main_repo_dirty_abort_failed"


class RestartScope(StrEnum):
    FULL_RESTART = "full_restart"
    PARTIAL_RESTART = "partial_restart"


class SkillSource(StrEnum):
    BUNDLED = "bundled"
    BUNDLED_EXTENDED = "bundled_extended"


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


class TerminationReason(StrEnum):
    """How a managed subprocess ended.

    Propagates termination provenance from run_managed_async to consumers,
    replacing implicit inference from exit codes.
    """

    NATURAL_EXIT = "natural_exit"
    COMPLETED = "completed"
    STALE = "stale"
    TIMED_OUT = "timed_out"


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
    """

    CHANNEL_A = "channel_a"
    CHANNEL_B = "channel_b"
    UNMONITORED = "unmonitored"


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
    """

    COMPLETION = "completion"
    STALE = "stale"
