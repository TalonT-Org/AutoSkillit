"""Core StrEnum discriminators.

Zero autoskillit imports. Provides the shared enum vocabulary for all higher layers.
"""

from __future__ import annotations

from enum import StrEnum, unique

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
    "TerminationAction",
    "KillReason",
    "ChannelConfirmation",
    "SessionOutcome",
    "CliSubtype",
    "ChannelBStatus",
    "PRState",
    "SessionType",
    "FleetErrorCode",
    "FeatureLifecycle",
    "DispatchGateType",
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
    CLONE_CONTAMINATION = "clone_contamination"


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
    MERGE_SUCCEEDED_CLEANUP_BLOCKED = "merge_succeeded_cleanup_blocked"


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
    LEAF         -- L1 headless worker (or L0 subagent -- both are terminal from
                    AutoSkillit's perspective since neither can call run_skill).
                    L0 subagents never set AUTOSKILLIT_SESSION_TYPE so they share
                    this terminal slot rather than having a distinct enum value.

    Note: interactive L1 sessions (autoskillit cook, bare Claude Code) have no
    SessionType value -- they bypass tier checks because AUTOSKILLIT_HEADLESS is unset.
    """

    FLEET = "fleet"
    ORCHESTRATOR = "orchestrator"
    LEAF = "leaf"


@unique
class FleetErrorCode(StrEnum):
    """Registered error codes for fleet dispatch failures.

    Every fleet error envelope must use one of these codes.
    Unregistered codes are rejected by fleet_error() at runtime.
    """

    FLEET_PARALLEL_REFUSED = "fleet_parallel_refused"
    FLEET_UNKNOWN_INGREDIENT = "fleet_unknown_ingredient"
    FLEET_RECIPE_NOT_FOUND = "fleet_recipe_not_found"
    FLEET_INVALID_RECIPE_KIND = "fleet_invalid_recipe_kind"
    FLEET_HARD_REFUSAL_HEADLESS = "fleet_hard_refusal_headless"
    FLEET_FEATURE_DISABLED = "fleet_feature_disabled"
    FLEET_MANIFEST_MISSING = "fleet_manifest_missing"
    FLEET_MANIFEST_CORRUPTED = "fleet_manifest_corrupted"
    FLEET_LOCK_NOT_INITIALIZED = "fleet_lock_not_initialized"
    FLEET_L2_TIMEOUT = "fleet_l2_timeout"
    FLEET_L2_NO_RESULT_BLOCK = "fleet_l2_no_result_block"
    FLEET_L2_PARSE_FAILED = "fleet_l2_parse_failed"
    FLEET_L2_STARTUP_OR_CRASH = "fleet_l2_startup_or_crash"
    FLEET_BUDGET_EXCEEDED = "fleet_budget_exceeded"
    FLEET_QUOTA_EXHAUSTED = "fleet_quota_exhausted"
    FLEET_CLEANUP_FAILED = "fleet_cleanup_failed"
    FLEET_GATE_UNKNOWN_DISPATCH = "fleet_gate_unknown_dispatch"
    FLEET_GATE_ALREADY_RECORDED = "fleet_gate_already_recorded"
    FLEET_GATE_NO_CAMPAIGN = "fleet_gate_no_campaign"


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


@unique
class DispatchGateType(StrEnum):
    """Valid gate types for campaign dispatch entries."""

    CONFIRM = "confirm"
