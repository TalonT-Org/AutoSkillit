"""execution/ IL-1 package: subprocess lifecycle, session parsing, headless runner, testing, DB.

Re-exports the full public surface of the six execution sub-modules.
All sub-modules depend only on autoskillit.core.* at runtime;
execution/headless.py has TYPE_CHECKING-only references to pipeline/.
"""

from autoskillit.core import CmdSpec, SkillResult
from autoskillit.execution._recording_skills import (
    restore_skill_snapshot,
    scan_skill_snapshots,
    snapshot_skill_dir,
)
from autoskillit.execution._session_log_recovery import recover_crashed_sessions
from autoskillit.execution.anomaly_detection import (
    AnomalyKind,
    AnomalySeverity,
    detect_anomalies,
)
from autoskillit.execution.backends import (
    BACKEND_REGISTRY,
    CODEX_AUTO_COMPACT_LIMIT,
    CODEX_HISTORY_RETENTION_TOKEN_LIMIT,
    CODEX_LIMITS_LAST_VERIFIED_VERSION,
    CODEX_MCP_REQUIRED_KEYS,
    CODEX_MCP_STARTUP_TIMEOUT_SEC,
    CODEX_MCP_TOOL_TIMEOUT_FLOOR,
    CODEX_RECIPE_DELIVERY_BUDGET,
    CODEX_RECIPE_DELIVERY_CALLING_CONTRACT,
    CODEX_RECIPE_DELIVERY_CALLING_CONTRACT_DIGEST,
    SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY,
    ClaudeCodeBackend,
    CodexAttestationResult,
    CodexBackend,
    CodexHostCorrelation,
    CodexOuterBudgetAttestor,
    CodexStateReadinessProbe,
    NullProtectedHostAttestationProvider,
    ProtectedHostAttestationProvider,
    ProtectedStoreAuthority,
    RecipeDeliveryReceiptLedger,
    RecipeReceiptHandle,
    RecipeReservationResult,
    _is_autoskillit_hook_entry,
    _is_autoskillit_registered,
    _read_codex_config,
    _serialize_toml,
    _write_codex_config,
    all_backends,
    codex_recipe_delivery_calling_contract,
    ensure_codex_mcp_registered,
    enumerate_fresh_codex_marker_ids,
    generate_codex_hooks_config,
    get_backend,
    read_rollout_thread_id,
    resolve_unique_codex_host_correlation,
    sync_hooks_to_codex_config,
)
from autoskillit.execution.backends._codex_prelaunch import codex_prelaunch_transaction
from autoskillit.execution.ci import DefaultCIWatcher
from autoskillit.execution.commands import ClaudeHeadlessCmd
from autoskillit.execution.db import (
    DefaultDatabaseReader,
)
from autoskillit.execution.db import (
    _execute_readonly_query as execute_readonly_query,
)
from autoskillit.execution.diff_annotator import (
    DiffMetrics,
    FilterResult,
    annotate_diff,
    compute_diff_metrics,
    extract_code_region,
    extract_valid_lines,
    filter_findings,
    parse_hunk_ranges,
    select_review_agents,
)
from autoskillit.execution.github import (
    DefaultGitHubFetcher,
    github_headers,
    parse_merge_queue_response,
)
from autoskillit.execution.github_review import (
    DefaultGitHubReviewGateway,
    DefaultGitHubReviewPoster,
    GitHubReviewLedger,
    GitHubReviewMutationCoordinator,
    canonicalize_review_request,
    compute_review_operation_key,
)
from autoskillit.execution.headless import (
    DefaultHeadlessExecutor,
    assert_interactive_ordering,
    run_headless_core,
)
from autoskillit.execution.launch_resolution import DefaultLaunchResolver
from autoskillit.execution.linux_tracing import (
    LINUX_TRACING_AVAILABLE,
    LinuxTracingHandle,
    ProcSnapshot,
    read_boot_id,
    read_starttime_ticks,
    start_linux_tracing,
)
from autoskillit.execution.merge_queue import DefaultMergeQueueWatcher, fetch_repo_merge_state
from autoskillit.execution.pr_analysis import (
    DOMAIN_PATHS,
    extract_linked_issues,
    is_valid_fidelity_finding,
    partition_files_by_domain,
)
from autoskillit.execution.process import (
    CaptureReadError,
    CaptureSetupError,
    DefaultSubprocessRunner,
    _has_active_execution_marker,  # noqa: F401 — re-exported for cli/app.py signal guard
    async_kill_process_tree,
    kill_process_tree,
    run_managed_async,
    run_managed_sync,
    summarize_capture,
)
from autoskillit.execution.quota import (
    QUOTA_CACHE_SCHEMA_VERSION,
    QuotaStatus,
    _refresh_quota_cache,  # noqa: F401 — re-exported for server consumers; not in __all__
    check_and_sleep_if_needed,
    invalidate_cache,
)
from autoskillit.execution.recording import (
    RECORD_SCENARIO_DIR_ENV,
    RECORD_SCENARIO_ENV,
    RECORD_SCENARIO_RECIPE_ENV,
    REPLAY_SCENARIO_DIR_ENV,
    REPLAY_SCENARIO_ENV,
    SCENARIO_STEP_NAME_ENV,
    RecordingSubprocessRunner,
    ReplayingSubprocessRunner,
    ScenarioReplayError,
    build_replay_runner,
)
from autoskillit.execution.remote_resolver import (
    REMOTE_PRECEDENCE,
    resolve_remote_name,
    resolve_remote_repo,
)
from autoskillit.execution.session import (
    ClaudeSessionResult,
    ContentState,
    DefaultManagedHeadlessSessionLineageStore,
    DefaultSkillSessionContractStore,
    ManagedHeadlessSessionLineageCASMismatch,
    ManagedHeadlessSessionLineageConflictError,
    SessionState,
    SkillSessionContract,
    _collapse_hr_split_delimiters,  # noqa: F401 — re-exported for fleet.result_parser
    classify_infra_exit,
    clear_session_state,
    delete_skill_session_contracts,
    extract_token_usage,
    parse_session_result,
    persist_session_state,
    read_session_state,
)
from autoskillit.execution.session_log import (
    flush_session_log,
    read_telemetry_clear_marker,
    resolve_log_dir,
    write_telemetry_clear_marker,
)
from autoskillit.execution.testing import (
    DefaultTestRunner,
    check_test_passed,
    condense_test_output,
    parse_pytest_summary,
)

__all__ = [
    # _process_kill
    "kill_process_tree",
    "async_kill_process_tree",
    # commands
    "CmdSpec",
    "ClaudeHeadlessCmd",
    # process
    "CaptureReadError",
    "CaptureSetupError",
    "DefaultSubprocessRunner",
    "run_managed_async",
    "run_managed_sync",
    "summarize_capture",
    # recording
    "RecordingSubprocessRunner",
    "ReplayingSubprocessRunner",
    "ScenarioReplayError",
    "build_replay_runner",
    "RECORD_SCENARIO_ENV",
    "RECORD_SCENARIO_DIR_ENV",
    "RECORD_SCENARIO_RECIPE_ENV",
    "REPLAY_SCENARIO_ENV",
    "REPLAY_SCENARIO_DIR_ENV",
    "SCENARIO_STEP_NAME_ENV",
    "restore_skill_snapshot",
    "scan_skill_snapshots",
    "snapshot_skill_dir",
    # quota
    "QUOTA_CACHE_SCHEMA_VERSION",
    "QuotaStatus",
    "check_and_sleep_if_needed",
    "invalidate_cache",
    # session
    "ClaudeSessionResult",
    "ContentState",
    "DefaultManagedHeadlessSessionLineageStore",
    "DefaultSkillSessionContractStore",
    "ManagedHeadlessSessionLineageCASMismatch",
    "ManagedHeadlessSessionLineageConflictError",
    "SessionState",
    "SkillSessionContract",
    "SkillResult",
    "classify_infra_exit",
    "clear_session_state",
    "delete_skill_session_contracts",
    "extract_token_usage",
    "parse_session_result",
    "persist_session_state",
    "read_session_state",
    # headless
    "run_headless_core",
    "DefaultHeadlessExecutor",
    "DefaultLaunchResolver",
    "assert_interactive_ordering",
    # testing
    "parse_pytest_summary",
    "check_test_passed",
    "DefaultTestRunner",
    "condense_test_output",
    # ci
    "DefaultCIWatcher",
    # merge_queue
    "DefaultMergeQueueWatcher",
    "fetch_repo_merge_state",
    # remote_resolver
    "REMOTE_PRECEDENCE",
    "resolve_remote_name",
    "resolve_remote_repo",
    # diff_annotator
    "DiffMetrics",
    "FilterResult",
    "annotate_diff",
    "compute_diff_metrics",
    "extract_code_region",
    "extract_valid_lines",
    "filter_findings",
    "parse_hunk_ranges",
    "select_review_agents",
    # db
    "execute_readonly_query",
    "DefaultDatabaseReader",
    # github
    "DefaultGitHubFetcher",
    "github_headers",
    "parse_merge_queue_response",
    # github_review
    "DefaultGitHubReviewGateway",
    "DefaultGitHubReviewPoster",
    "GitHubReviewLedger",
    "GitHubReviewMutationCoordinator",
    "canonicalize_review_request",
    "compute_review_operation_key",
    # linux_tracing
    "LINUX_TRACING_AVAILABLE",
    "LinuxTracingHandle",
    "ProcSnapshot",
    "read_boot_id",
    "read_starttime_ticks",
    "start_linux_tracing",
    # backends
    "BACKEND_REGISTRY",
    "CODEX_MCP_REQUIRED_KEYS",
    "CODEX_MCP_STARTUP_TIMEOUT_SEC",
    "CODEX_MCP_TOOL_TIMEOUT_FLOOR",
    "CODEX_HISTORY_RETENTION_TOKEN_LIMIT",
    "CODEX_RECIPE_DELIVERY_BUDGET",
    "CODEX_RECIPE_DELIVERY_CALLING_CONTRACT",
    "CODEX_RECIPE_DELIVERY_CALLING_CONTRACT_DIGEST",
    "SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY",
    "CODEX_LIMITS_LAST_VERIFIED_VERSION",
    "CODEX_AUTO_COMPACT_LIMIT",
    "ClaudeCodeBackend",
    "CodexAttestationResult",
    "CodexBackend",
    "CodexHostCorrelation",
    "CodexOuterBudgetAttestor",
    "CodexStateReadinessProbe",
    "NullProtectedHostAttestationProvider",
    "ProtectedHostAttestationProvider",
    "ProtectedStoreAuthority",
    "RecipeDeliveryReceiptLedger",
    "RecipeReceiptHandle",
    "RecipeReservationResult",
    "_is_autoskillit_hook_entry",
    "_is_autoskillit_registered",
    "_read_codex_config",
    "_serialize_toml",
    "_write_codex_config",
    "codex_recipe_delivery_calling_contract",
    "codex_prelaunch_transaction",
    "ensure_codex_mcp_registered",
    "enumerate_fresh_codex_marker_ids",
    "generate_codex_hooks_config",
    "sync_hooks_to_codex_config",
    "read_rollout_thread_id",
    "resolve_unique_codex_host_correlation",
    # anomaly_detection
    "detect_anomalies",
    "AnomalyKind",
    "AnomalySeverity",
    # session_log
    "flush_session_log",
    "read_telemetry_clear_marker",
    "recover_crashed_sessions",
    "resolve_log_dir",
    "write_telemetry_clear_marker",
    # pr_analysis
    "DOMAIN_PATHS",
    "extract_linked_issues",
    "is_valid_fidelity_finding",
    "partition_files_by_domain",
    # backends
    "all_backends",
    "get_backend",
]
