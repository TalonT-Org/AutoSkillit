"""execution/ L1 package: subprocess lifecycle, session parsing, headless runner, testing, DB.

Re-exports the full public surface of the six execution sub-modules.
All sub-modules depend only on autoskillit.core.* at runtime;
execution/headless.py has TYPE_CHECKING-only references to pipeline/.
"""

from autoskillit.core import SkillResult
from autoskillit.execution.anomaly_detection import (
    AnomalyKind,
    AnomalySeverity,
    detect_anomalies,
)
from autoskillit.execution.ci import DefaultCIWatcher
from autoskillit.execution.commands import (
    ClaudeHeadlessCmd,
    ClaudeInteractiveCmd,
    build_headless_cmd,
    build_interactive_cmd,
)
from autoskillit.execution.db import (
    DefaultDatabaseReader,
)
from autoskillit.execution.db import (
    _execute_readonly_query as execute_readonly_query,
)
from autoskillit.execution.diff_annotator import (
    FilterResult,
    annotate_diff,
    filter_findings,
    parse_hunk_ranges,
)
from autoskillit.execution.github import (
    DefaultGitHubFetcher,
    github_headers,
    parse_merge_queue_response,
)
from autoskillit.execution.headless import (
    DefaultHeadlessExecutor,
    run_headless_core,
)
from autoskillit.execution.linux_tracing import (
    LINUX_TRACING_AVAILABLE,
    LinuxTracingHandle,
    ProcSnapshot,
    read_boot_id,
    read_starttime_ticks,
    start_linux_tracing,
)
from autoskillit.execution.merge_queue import DefaultMergeQueueWatcher
from autoskillit.execution.pr_analysis import (
    DOMAIN_PATHS,
    extract_linked_issues,
    is_valid_fidelity_finding,
    partition_files_by_domain,
)
from autoskillit.execution.process import (
    DefaultSubprocessRunner,
    run_managed_async,
    run_managed_sync,
)
from autoskillit.execution.quota import (
    QuotaStatus,
    _refresh_quota_cache,  # noqa: F401 — imported for re-export via server.helpers; not in __all__
    check_and_sleep_if_needed,
)
from autoskillit.execution.recording import (
    RECORD_SCENARIO_DIR_ENV,
    RECORD_SCENARIO_ENV,
    RECORD_SCENARIO_RECIPE_ENV,
    SCENARIO_STEP_NAME_ENV,
    RecordingSubprocessRunner,
)
from autoskillit.execution.remote_resolver import REMOTE_PRECEDENCE, resolve_remote_repo
from autoskillit.execution.session import (
    ClaudeSessionResult,
    ContentState,
    extract_token_usage,
    parse_session_result,
)
from autoskillit.execution.session_log import (
    flush_session_log,
    read_telemetry_clear_marker,
    recover_crashed_sessions,
    resolve_log_dir,
    write_telemetry_clear_marker,
)
from autoskillit.execution.testing import (
    DefaultTestRunner,
    check_test_passed,
    parse_pytest_summary,
)

__all__ = [
    # commands
    "ClaudeInteractiveCmd",
    "ClaudeHeadlessCmd",
    "build_interactive_cmd",
    "build_headless_cmd",
    # process
    "DefaultSubprocessRunner",
    "run_managed_async",
    "run_managed_sync",
    # recording
    "RecordingSubprocessRunner",
    "RECORD_SCENARIO_ENV",
    "RECORD_SCENARIO_DIR_ENV",
    "RECORD_SCENARIO_RECIPE_ENV",
    "SCENARIO_STEP_NAME_ENV",
    # quota
    "QuotaStatus",
    "check_and_sleep_if_needed",
    # session
    "ClaudeSessionResult",
    "ContentState",
    "SkillResult",
    "extract_token_usage",
    "parse_session_result",
    # headless
    "run_headless_core",
    "DefaultHeadlessExecutor",
    # testing
    "parse_pytest_summary",
    "check_test_passed",
    "DefaultTestRunner",
    # ci
    "DefaultCIWatcher",
    # merge_queue
    "DefaultMergeQueueWatcher",
    # remote_resolver
    "REMOTE_PRECEDENCE",
    "resolve_remote_repo",
    # diff_annotator
    "FilterResult",
    "annotate_diff",
    "filter_findings",
    "parse_hunk_ranges",
    # db
    "execute_readonly_query",
    "DefaultDatabaseReader",
    # github
    "DefaultGitHubFetcher",
    "github_headers",
    "parse_merge_queue_response",
    # linux_tracing
    "LINUX_TRACING_AVAILABLE",
    "LinuxTracingHandle",
    "ProcSnapshot",
    "read_boot_id",
    "read_starttime_ticks",
    "start_linux_tracing",
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
]
