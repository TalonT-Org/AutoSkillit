"""L0 foundation sub-package: types, logging, and I/O primitives.

Re-exports the full public surface of core.types, core.logging, and core.io
so callers can do either `from autoskillit.core import get_logger` or the
explicit `from autoskillit.core.logging import get_logger`.
"""

from ._claude_env import build_claude_env
from ._plugin_cache import (
    _InstallLock as _InstallLock,
)
from ._plugin_cache import (
    _retire_old_versions as _retire_old_versions,
)
from ._plugin_cache import (
    any_kitchen_open as any_kitchen_open,
)
from ._plugin_cache import (
    append_retiring_entry as append_retiring_entry,
)
from ._plugin_cache import (
    clear_kitchens_for_pid as clear_kitchens_for_pid,
)
from ._plugin_cache import (
    register_active_kitchen as register_active_kitchen,
)
from ._plugin_cache import (
    sweep_retiring_cache as sweep_retiring_cache,
)
from ._plugin_cache import (
    unregister_active_kitchen as unregister_active_kitchen,
)
from ._plugin_ids import DIRECT_PREFIX as DIRECT_PREFIX
from ._plugin_ids import MARKETPLACE_PREFIX as MARKETPLACE_PREFIX
from ._plugin_ids import detect_autoskillit_mcp_prefix as detect_autoskillit_mcp_prefix
from ._terminal_table import TerminalColumn as TerminalColumn
from ._terminal_table import _render_gfm_table as _render_gfm_table
from ._terminal_table import _render_terminal_table as _render_terminal_table
from ._version_snapshot import collect_version_snapshot as collect_version_snapshot
from .branch_guard import is_protected_branch
from .claude_conventions import ClaudeDirectoryConventions, LayoutError, validate_add_dir
from .github_url import _parse_issue_ref as _parse_issue_ref
from .github_url import normalize_owner_repo as normalize_owner_repo
from .github_url import parse_github_repo as parse_github_repo
from .io import (
    _AUTOSKILLIT_GITIGNORE_ENTRIES as _AUTOSKILLIT_GITIGNORE_ENTRIES,
)
from .io import (
    _COMMITTED_BY_DESIGN as _COMMITTED_BY_DESIGN,
)
from .io import (
    YAMLError,
    atomic_write,
    dump_yaml_str,
    ensure_project_temp,
    load_yaml,
    resolve_temp_dir,
    temp_dir_display_str,
    write_versioned_json,
)
from .logging import (
    configure_logging,
    get_logger,
)
from .paths import (
    GENERATED_FILES,
    claude_code_log_path,
    claude_code_project_dir,
    find_latest_session_id,
    is_git_main_checkout,
    is_git_worktree,
    pkg_root,
)
from .readiness import (
    cleanup_readiness_sentinel,
    readiness_sentinel_path,
    write_readiness_sentinel,
)
from .types import (
    AUTOSKILLIT_INSTALLED_VERSION,
    AUTOSKILLIT_PRIVATE_ENV_VARS,
    AUTOSKILLIT_SKILL_PREFIX,
    CAMPAIGN_ID_ENV_VAR,
    CATEGORY_TAGS,
    CONTEXT_EXHAUSTION_MARKER,
    FRANCHISE_TOOLS,
    FREE_RANGE_TOOLS,
    GATED_TOOLS,
    HEADLESS_ENV_VAR,
    HEADLESS_TOOLS,
    KITCHEN_SESSION_ID_ENV_VAR,
    PACK_REGISTRY,
    PIPELINE_FORBIDDEN_TOOLS,
    RECIPE_PACK_REGISTRY,
    RECIPE_PACK_TAGS,
    RESERVED_LOG_RECORD_KEYS,
    RETIRED_READINESS_TOKENS,
    SESSION_TYPE_ENV_VAR,
    SESSION_TYPE_FRANCHISE,
    SESSION_TYPE_LEAF,
    SESSION_TYPE_ORCHESTRATOR,
    SKILL_COMMAND_PREFIX,
    SKILL_TOOLS,
    TOOL_SUBSET_TAGS,
    UNGATED_TOOLS,
    AuditLog,
    BackgroundSupervisor,
    ChannelBStatus,
    ChannelConfirmation,
    CIRunScope,
    CIWatcher,
    ClaudeFlags,
    CleanupResult,
    CliSubtype,
    CloneGateUncommitted,
    CloneGateUnpublished,
    CloneManager,
    CloneResult,
    CloneSuccessResult,
    DatabaseReader,
    FailureRecord,
    FranchiseLock,
    GateState,
    GitHubFetcher,
    HeadlessExecutor,
    KillReason,
    LoadReport,
    LoadResult,
    McpResponseLog,
    MergeFailedStep,
    MergeQueueWatcher,
    MergeState,
    MigrationService,
    OutputFormat,
    OutputPatternResolver,
    PackDef,
    PRState,
    QuotaRefreshTask,
    RecipePackDef,
    RecipeRepository,
    RecipeSource,
    RestartScope,
    RetryReason,
    SessionOutcome,
    SessionSkillManager,
    SessionType,
    Severity,
    SkillLister,
    SkillResolver,
    SkillResult,
    SkillSource,
    SubprocessResult,
    SubprocessRunner,
    SupportsLogger,
    TerminationAction,
    TerminationReason,
    TestResult,
    TestRunner,
    TimingLog,
    TokenFactory,
    TokenLog,
    ValidatedAddDir,
    WorkspaceManager,
    WriteBehaviorSpec,
    WriteExpectedResolver,
    extract_path_arg,
    extract_skill_name,
    resolve_target_skill,
    session_type,
    truncate_text,
)

__all__ = [
    # _claude_env
    "build_claude_env",
    # _terminal_table
    "TerminalColumn",
    "_render_gfm_table",
    "_render_terminal_table",
    # branch_guard
    "is_protected_branch",
    # claude_conventions
    "ClaudeDirectoryConventions",
    "LayoutError",
    "validate_add_dir",
    # github_url
    "normalize_owner_repo",
    "parse_github_repo",
    "_parse_issue_ref",
    # io
    "YAMLError",
    "atomic_write",
    "dump_yaml_str",
    "ensure_project_temp",
    "load_yaml",
    "resolve_temp_dir",
    "temp_dir_display_str",
    "write_versioned_json",
    # logging
    "configure_logging",
    "get_logger",
    # readiness
    "cleanup_readiness_sentinel",
    "readiness_sentinel_path",
    "write_readiness_sentinel",
    # paths
    "GENERATED_FILES",
    "claude_code_log_path",
    "claude_code_project_dir",
    "find_latest_session_id",
    "is_git_main_checkout",
    "is_git_worktree",
    "pkg_root",
    # types
    "ClaudeFlags",
    "ValidatedAddDir",
    "AUTOSKILLIT_INSTALLED_VERSION",
    "AUTOSKILLIT_PRIVATE_ENV_VARS",
    "AUTOSKILLIT_SKILL_PREFIX",
    "WriteBehaviorSpec",
    "WriteExpectedResolver",
    "extract_path_arg",
    "extract_skill_name",
    "resolve_target_skill",
    "PackDef",
    "PACK_REGISTRY",
    "RecipePackDef",
    "RECIPE_PACK_REGISTRY",
    "RECIPE_PACK_TAGS",
    "CATEGORY_TAGS",
    "TOOL_SUBSET_TAGS",
    "CONTEXT_EXHAUSTION_MARKER",
    "RETIRED_READINESS_TOKENS",
    "FRANCHISE_TOOLS",
    "FREE_RANGE_TOOLS",
    "GATED_TOOLS",
    "HEADLESS_TOOLS",
    "PIPELINE_FORBIDDEN_TOOLS",
    "RESERVED_LOG_RECORD_KEYS",
    "SKILL_COMMAND_PREFIX",
    "SKILL_TOOLS",
    "UNGATED_TOOLS",
    "AuditLog",
    "BackgroundSupervisor",
    "QuotaRefreshTask",
    "CIRunScope",
    "CIWatcher",
    "MergeQueueWatcher",
    "ChannelBStatus",
    "ChannelConfirmation",
    "CliSubtype",
    "CleanupResult",
    "CloneGateUncommitted",
    "CloneGateUnpublished",
    "CloneManager",
    "CloneResult",
    "CloneSuccessResult",
    "DatabaseReader",
    "FailureRecord",
    "FranchiseLock",
    "GateState",
    "GitHubFetcher",
    "HeadlessExecutor",
    "LoadReport",
    "LoadResult",
    "MergeFailedStep",
    "MergeState",
    "PRState",
    "MigrationService",
    "OutputFormat",
    "OutputPatternResolver",
    "RecipeRepository",
    "RecipeSource",
    "RestartScope",
    "RetryReason",
    "SessionOutcome",
    # types (session)
    "SessionType",
    "session_type",
    "SESSION_TYPE_ENV_VAR",
    "SESSION_TYPE_FRANCHISE",
    "SESSION_TYPE_ORCHESTRATOR",
    "SESSION_TYPE_LEAF",
    "HEADLESS_ENV_VAR",
    "CAMPAIGN_ID_ENV_VAR",
    "KITCHEN_SESSION_ID_ENV_VAR",
    "Severity",
    "KillReason",
    "SkillResult",
    "SessionSkillManager",
    "SkillSource",
    "SubprocessResult",
    "SubprocessRunner",
    "SkillLister",
    "SkillResolver",
    "SupportsLogger",
    "TerminationAction",
    "TerminationReason",
    "TestResult",
    "TestRunner",
    "McpResponseLog",
    "TimingLog",
    "TokenFactory",
    "TokenLog",
    "WorkspaceManager",
    "truncate_text",
    # _version_snapshot
    "collect_version_snapshot",
    # _plugin_cache
    "any_kitchen_open",
    "append_retiring_entry",
    "clear_kitchens_for_pid",
    "register_active_kitchen",
    "sweep_retiring_cache",
    "unregister_active_kitchen",
    # _plugin_ids
    "DIRECT_PREFIX",
    "MARKETPLACE_PREFIX",
    "detect_autoskillit_mcp_prefix",
]
