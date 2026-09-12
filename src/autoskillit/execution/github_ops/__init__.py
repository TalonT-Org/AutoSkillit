"""execution/github_ops/ — GitHub API integration, CI watcher, diff annotation, remote resolver.

Re-exports the full public surface of the six moved modules.
"""

from autoskillit.execution.github_ops.ci import DefaultCIWatcher
from autoskillit.execution.github_ops.diff_annotator import (
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
    github_headers,
    parse_merge_queue_response,
)
from autoskillit.execution.github_ops.pr_analysis import (
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
