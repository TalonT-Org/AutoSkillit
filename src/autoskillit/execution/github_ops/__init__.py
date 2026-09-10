"""execution/github_ops/ — GitHub API integration, CI watcher, diff annotation, remote resolver.

Re-exports the full public surface of the six moved modules.
"""

from autoskillit.execution.github_ops._github_http import (
    github_error_message,  # noqa: F401
    is_secondary_rate_limit,  # noqa: F401
    retry_after_seconds,  # noqa: F401
)
from autoskillit.execution.github_ops.ci import (
    _BACKOFF_BANDS,  # noqa: F401
    FAILED_CONCLUSIONS,  # noqa: F401
    KNOWN_CI_CONCLUSIONS,  # noqa: F401
    DefaultCIWatcher,  # noqa: F401
    _jittered_sleep,  # noqa: F401
    _validate_run_matches_scope,  # noqa: F401
)
from autoskillit.execution.github_ops.diff_annotator import (
    _ALL_STANDARD_AGENTS,  # noqa: F401
    _FILE_HEADER,  # noqa: F401
    _HUNK_HEADER,  # noqa: F401
    _LINE_MARKER,  # noqa: F401
    _SMALL_DIFF_CORE_AGENTS,  # noqa: F401
    _STRUCTURAL_SUFFIXES,  # noqa: F401
    DiffMetrics,  # noqa: F401
    FilterResult,  # noqa: F401
    annotate_diff,  # noqa: F401
    compute_diff_metrics,  # noqa: F401
    extract_code_region,  # noqa: F401
    extract_valid_lines,  # noqa: F401
    filter_findings,  # noqa: F401
    parse_hunk_ranges,  # noqa: F401
    select_review_agents,  # noqa: F401
)
from autoskillit.execution.github_ops.github import (
    DefaultGitHubFetcher,  # noqa: F401
    _format_issue_markdown,  # noqa: F401
    _slugify,  # noqa: F401
    _TrackingTransport,  # noqa: F401
    github_headers,  # noqa: F401
    make_tracked_httpx_client,  # noqa: F401
    parse_merge_queue_response,  # noqa: F401
)
from autoskillit.execution.github_ops.pr_analysis import (
    _LINKED_ISSUE_PATTERN,  # noqa: F401
    _VALID_FIDELITY_SEVERITIES,  # noqa: F401
    DOMAIN_PATHS,  # noqa: F401
    extract_linked_issues,  # noqa: F401
    is_valid_fidelity_finding,  # noqa: F401
    partition_files_by_domain,  # noqa: F401
)
from autoskillit.execution.github_ops.remote_resolver import (
    REMOTE_PRECEDENCE,  # noqa: F401
    resolve_remote_name,  # noqa: F401
    resolve_remote_repo,  # noqa: F401
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
