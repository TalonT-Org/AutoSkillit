"""execution/runtime/ — launch, headless cmds, clone guard, test runner, DB reader.

Re-exports the full public surface of the five moved modules.
"""

from autoskillit.execution.runtime.clone_guard import (
    _GIT_TIMEOUT,  # noqa: F401
    CLONE_COMMIT_SKILLS,  # noqa: F401
    GUARD_EXCLUDE_PREFIX,  # noqa: F401
    CloneGuardPolicy,  # noqa: F401
    CloneSnapshot,  # noqa: F401
    ContaminationReport,  # noqa: F401
    _detect_new_worktrees,  # noqa: F401
    _parse_worktree_branches,  # noqa: F401
    _parse_worktree_list,  # noqa: F401
    _prune_stash_overflow,  # noqa: F401
    _recover_branch_name,  # noqa: F401
    _recover_worktree_path,  # noqa: F401
    _stash_and_clean,  # noqa: F401
    _status_path_under_prefix,  # noqa: F401
    build_clone_guard_policy,  # noqa: F401
    check_and_revert_clone_contamination,  # noqa: F401
    derive_exclude_prefix,  # noqa: F401
    detect_contamination,  # noqa: F401
    is_clone_commit_skill,  # noqa: F401
    is_path_under_exclude,  # noqa: F401
    is_worktree_skill,  # noqa: F401
    revert_contamination,  # noqa: F401
    snapshot_clone_state,  # noqa: F401
    validate_pre_session_index,  # noqa: F401
)
from autoskillit.execution.runtime.commands import ClaudeHeadlessCmd
from autoskillit.execution.runtime.db import (
    _ALLOWED_ACTIONS,  # noqa: F401
    _FORBIDDEN_SQL,  # noqa: F401
    _STRIP_SQL_COMMENTS,  # noqa: F401
    DefaultDatabaseReader,  # noqa: F401
    _execute_readonly_query,  # noqa: F401
    _row_to_dict,  # noqa: F401
    _select_only_authorizer,  # noqa: F401
    _validate_select_only,  # noqa: F401
)
from autoskillit.execution.runtime.db import (
    _execute_readonly_query as execute_readonly_query,
)
from autoskillit.execution.runtime.launch_resolution import (
    _CREDENTIAL_ENV_SUFFIXES,  # noqa: F401
    _DEFAULT_BACKEND_ALIASES,  # noqa: F401
    DefaultLaunchResolver,  # noqa: F401
    _default_source,  # noqa: F401
    _is_credential_environment_key,  # noqa: F401
)
from autoskillit.execution.runtime.testing import (
    _BARE_TIME_ANCHOR,  # noqa: F401
    _OUTCOME_PATTERN,  # noqa: F401
    _PROGRESS_PCT_RE,  # noqa: F401
    DefaultTestRunner,  # noqa: F401
    _is_progress_line,  # noqa: F401
    _matches_to_counts,  # noqa: F401
    _read_sidecar_base_branch,  # noqa: F401
    _resolve_base_ref,  # noqa: F401
    _strip_progress_noise,  # noqa: F401
    build_sanitized_env,  # noqa: F401
    check_test_passed,  # noqa: F401
    condense_test_output,  # noqa: F401
    extract_summary_line,  # noqa: F401
    parse_pytest_summary,  # noqa: F401
)

__all__ = [
    # launch_resolution
    "DefaultLaunchResolver",
    # commands
    "ClaudeHeadlessCmd",
    # testing
    "DefaultTestRunner",
    "build_sanitized_env",
    "check_test_passed",
    "condense_test_output",
    "parse_pytest_summary",
    # db
    "execute_readonly_query",
    "DefaultDatabaseReader",
]
