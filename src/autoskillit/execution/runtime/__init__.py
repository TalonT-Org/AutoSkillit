"""execution/runtime/ — launch resolution, headless commands, clone guard, test runner, database reader.

Re-exports the full public surface of the five moved modules.
"""

from autoskillit.execution.runtime.clone_guard import (
    CLONE_COMMIT_SKILLS,
    CloneGuardPolicy,
    CloneSnapshot,
    ContaminationReport,
    GUARD_EXCLUDE_PREFIX,
    _GIT_TIMEOUT,
    _detect_new_worktrees,
    _parse_worktree_branches,
    _parse_worktree_list,
    _prune_stash_overflow,
    _recover_branch_name,
    _recover_worktree_path,
    _stash_and_clean,
    _status_path_under_prefix,
    build_clone_guard_policy,
    check_and_revert_clone_contamination,
    derive_exclude_prefix,
    detect_contamination,
    is_clone_commit_skill,
    is_path_under_exclude,
    is_worktree_skill,
    revert_contamination,
    snapshot_clone_state,
    validate_pre_session_index,
)
from autoskillit.execution.runtime.commands import ClaudeHeadlessCmd
from autoskillit.execution.runtime.db import (
    DefaultDatabaseReader,
    _ALLOWED_ACTIONS,
    _FORBIDDEN_SQL,
    _STRIP_SQL_COMMENTS,
    _execute_readonly_query,
    _row_to_dict,
    _select_only_authorizer,
    _validate_select_only,
)
from autoskillit.execution.runtime.db import (
    _execute_readonly_query as execute_readonly_query,
)
from autoskillit.execution.runtime.launch_resolution import (
    DefaultLaunchResolver,
    _CREDENTIAL_ENV_SUFFIXES,
    _DEFAULT_BACKEND_ALIASES,
    _default_source,
    _is_credential_environment_key,
)
from autoskillit.execution.runtime.testing import (
    DefaultTestRunner,
    _BARE_TIME_ANCHOR,
    _OUTCOME_PATTERN,
    _PROGRESS_PCT_RE,
    _is_progress_line,
    _matches_to_counts,
    _read_sidecar_base_branch,
    _resolve_base_ref,
    _strip_progress_noise,
    build_sanitized_env,
    check_test_passed,
    condense_test_output,
    extract_summary_line,
    parse_pytest_summary,
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
