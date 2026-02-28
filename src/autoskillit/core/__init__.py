"""L0 foundation sub-package: types, logging, and I/O primitives.

Re-exports the full public surface of core.types, core.logging, and core.io
so callers can do either `from autoskillit.core import get_logger` or the
explicit `from autoskillit.core.logging import get_logger`.
"""

from .io import (
    YAMLError,
    _atomic_write,
    dump_yaml_str,
    ensure_project_temp,
    load_yaml,
)
from .logging import (
    configure_logging,
    get_logger,
)
from .types import (
    CONTEXT_EXHAUSTION_MARKER,
    PIPELINE_FORBIDDEN_TOOLS,
    RESERVED_LOG_RECORD_KEYS,
    RETRY_RESPONSE_FIELDS,
    SKILL_TOOLS,
    AuditStore,
    CleanupResult,
    CloneManager,
    DatabaseReader,
    FailureRecord,
    GatePolicy,
    GitHubFetcher,
    HeadlessExecutor,
    LoadReport,
    LoadResult,
    MergeFailedStep,
    MergeState,
    MigrationService,
    RecipeRepository,
    RecipeSource,
    RestartScope,
    RetryReason,
    Severity,
    SkillResult,
    SkillSource,
    SubprocessResult,
    SubprocessRunner,
    TerminationReason,
    TestRunner,
    TokenStore,
    WorkspaceManager,
    truncate_text,
)

__all__ = [
    # io
    "YAMLError",
    "_atomic_write",
    "dump_yaml_str",
    "ensure_project_temp",
    "load_yaml",
    # logging
    "configure_logging",
    "get_logger",
    # types
    "CONTEXT_EXHAUSTION_MARKER",
    "PIPELINE_FORBIDDEN_TOOLS",
    "RESERVED_LOG_RECORD_KEYS",
    "RETRY_RESPONSE_FIELDS",
    "SKILL_TOOLS",
    "AuditStore",
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
    "RecipeRepository",
    "RecipeSource",
    "RestartScope",
    "RetryReason",
    "Severity",
    "SkillResult",
    "SkillSource",
    "SubprocessResult",
    "SubprocessRunner",
    "TerminationReason",
    "TestRunner",
    "TokenStore",
    "WorkspaceManager",
    "truncate_text",
]
