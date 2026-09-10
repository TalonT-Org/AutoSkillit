"""execution/github_ops/ — GitHub API integration, CI watcher, diff annotation, remote resolver.

Re-exports the full public surface of the six moved modules.
"""

from autoskillit.execution.github_ops._github_http import (
    github_error_message,
    is_secondary_rate_limit,
    retry_after_seconds,
)
from autoskillit.execution.github_ops.ci import (
    _BACKOFF_BANDS,
    FAILED_CONCLUSIONS,
    KNOWN_CI_CONCLUSIONS,
    DefaultCIWatcher,
    _jittered_sleep,
    _validate_run_matches_scope,
)
from autoskillit.execution.github_ops.diff_annotator import (
    _ALL_STANDARD_AGENTS,
    _FILE_HEADER,
    _HUNK_HEADER,
    _LINE_MARKER,
    _SMALL_DIFF_CORE_AGENTS,
    _STRUCTURAL_SUFFIXES,
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
from autoskillit.execution.github_ops.github import (
    DefaultGitHubFetcher,
    _format_issue_markdown,
    _slugify,
    _TrackingTransport,
    github_headers,
    make_tracked_httpx_client,
    parse_merge_queue_response,
)
from autoskillit.execution.github_ops.pr_analysis import (
    _LINKED_ISSUE_PATTERN,
    _VALID_FIDELITY_SEVERITIES,
    DOMAIN_PATHS,
    extract_linked_issues,
    is_valid_fidelity_finding,
    partition_files_by_domain,
)
from autoskillit.execution.github_ops.remote_resolver import (
    REMOTE_PRECEDENCE,
    resolve_remote_name,
    resolve_remote_repo,
)

__all__ = [
    # ci
    "DefaultCIWatcher",
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
    # github
    "DefaultGitHubFetcher",
    "github_headers",
    "parse_merge_queue_response",
    # pr_analysis
    "DOMAIN_PATHS",
    "extract_linked_issues",
    "is_valid_fidelity_finding",
    "partition_files_by_domain",
]
