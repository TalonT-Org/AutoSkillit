"""L0 foundation sub-package: types, logging, and I/O primitives.

Re-exports the full public surface of core.types, core.logging, and core.io
so callers can do either `from autoskillit.core import get_logger` or the
explicit `from autoskillit.core.logging import get_logger`.
"""

from ._terminal_table import TerminalColumn as TerminalColumn
from ._terminal_table import _render_gfm_table as _render_gfm_table
from ._terminal_table import _render_terminal_table as _render_terminal_table
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
    _ROOT_GITIGNORE_ENTRIES as _ROOT_GITIGNORE_ENTRIES,
)
from .io import (
    YAMLError,
    atomic_write,
    dump_yaml_str,
    ensure_project_temp,
    load_yaml,
)
from .logging import (
    configure_logging,
    get_logger,
)
from .paths import (
    GENERATED_FILES,
    claude_code_log_path,
    claude_code_project_dir,
    is_git_worktree,
    pkg_root,
)
from .types import (
    AUTOSKILLIT_INSTALLED_VERSION,
    AUTOSKILLIT_PRIVATE_ENV_VARS,
    AUTOSKILLIT_SKILL_PREFIX,
    CATEGORY_TAGS,
    CONTEXT_EXHAUSTION_MARKER,
    FREE_RANGE_TOOLS,
    GATED_TOOLS,
    HEADLESS_TOOLS,
    PIPELINE_FORBIDDEN_TOOLS,
    RESERVED_LOG_RECORD_KEYS,
    SKILL_COMMAND_PREFIX,
    SKILL_TOOLS,
    TOOL_CATEGORIES,
    TOOL_SUBSET_TAGS,
    UNGATED_TOOLS,
    AuditStore,
    BackgroundSupervisor,
    ChannelBStatus,
    ChannelConfirmation,
    CIRunScope,
    CIWatcher,
    ClaudeFlags,
    CleanupResult,
    CliSubtype,
    CloneManager,
    DatabaseReader,
    FailureRecord,
    GatePolicy,
    GitHubFetcher,
    HeadlessExecutor,
    LoadReport,
    LoadResult,
    McpResponseStore,
    MergeFailedStep,
    MergeQueueWatcher,
    MergeState,
    MigrationService,
    OutputFormat,
    OutputPatternResolver,
    RecipeRepository,
    RecipeSource,
    RestartScope,
    RetryReason,
    SessionOutcome,
    SessionSkillManager,
    Severity,
    SkillResult,
    SkillSource,
    SubprocessResult,
    SubprocessRunner,
    TargetSkillResolver,
    TerminationReason,
    TestRunner,
    TimingStore,
    TokenStore,
    ValidatedAddDir,
    WorkspaceManager,
    WriteBehaviorSpec,
    WriteExpectedResolver,
    extract_skill_name,
    resolve_target_skill,
    truncate_text,
)

__all__ = [
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
    "_ROOT_GITIGNORE_ENTRIES",
    # logging
    "configure_logging",
    "get_logger",
    # paths
    "GENERATED_FILES",
    "claude_code_log_path",
    "claude_code_project_dir",
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
    "extract_skill_name",
    "resolve_target_skill",
    "CATEGORY_TAGS",
    "TOOL_SUBSET_TAGS",
    "CONTEXT_EXHAUSTION_MARKER",
    "FREE_RANGE_TOOLS",
    "GATED_TOOLS",
    "HEADLESS_TOOLS",
    "PIPELINE_FORBIDDEN_TOOLS",
    "RESERVED_LOG_RECORD_KEYS",
    "SKILL_COMMAND_PREFIX",
    "SKILL_TOOLS",
    "TOOL_CATEGORIES",
    "UNGATED_TOOLS",
    "AuditStore",
    "BackgroundSupervisor",
    "CIRunScope",
    "CIWatcher",
    "MergeQueueWatcher",
    "ChannelBStatus",
    "ChannelConfirmation",
    "CliSubtype",
    "CleanupResult",
    "CloneManager",
    "DatabaseReader",
    "FailureRecord",
    "GatePolicy",
    "GitHubFetcher",
    "HeadlessExecutor",
    "LoadReport",
    "LoadResult",
    "MergeFailedStep",
    "MergeState",
    "MigrationService",
    "OutputFormat",
    "OutputPatternResolver",
    "RecipeRepository",
    "RecipeSource",
    "RestartScope",
    "RetryReason",
    "SessionOutcome",
    "Severity",
    "SkillResult",
    "SessionSkillManager",
    "SkillSource",
    "SubprocessResult",
    "SubprocessRunner",
    "TargetSkillResolver",
    "TerminationReason",
    "TestRunner",
    "McpResponseStore",
    "TimingStore",
    "TokenStore",
    "WorkspaceManager",
    "truncate_text",
]
