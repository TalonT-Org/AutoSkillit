"""L0 foundation sub-package: types, logging, and I/O primitives.

Re-exports the full public surface of core.types, core.logging, and core.io
so callers can do either `from autoskillit.core import get_logger` or the
explicit `from autoskillit.core.logging import get_logger`.
"""

from autoskillit.core.io import (
    YAMLError,
    _atomic_write,
    dump_yaml,
    dump_yaml_str,
    ensure_project_temp,
    load_yaml,
)
from autoskillit.core.logging import (
    PACKAGE_LOGGER_NAME,
    configure_logging,
    get_logger,
)
from autoskillit.core.types import (
    CONTEXT_EXHAUSTION_MARKER,
    PIPELINE_FORBIDDEN_TOOLS,
    RETRY_RESPONSE_FIELDS,
    SKILL_TOOLS,
    FailureRecord,
    LoadReport,
    LoadResult,
    MergeFailedStep,
    MergeState,
    RecipeSource,
    RestartScope,
    RetryReason,
    Severity,
    SkillSource,
    SubprocessResult,
    SubprocessRunner,
    T,
    TerminationReason,
)

__all__ = [
    # io
    "YAMLError",
    "_atomic_write",
    "dump_yaml",
    "dump_yaml_str",
    "ensure_project_temp",
    "load_yaml",
    # logging
    "PACKAGE_LOGGER_NAME",
    "configure_logging",
    "get_logger",
    # types
    "CONTEXT_EXHAUSTION_MARKER",
    "PIPELINE_FORBIDDEN_TOOLS",
    "RETRY_RESPONSE_FIELDS",
    "SKILL_TOOLS",
    "FailureRecord",
    "LoadReport",
    "LoadResult",
    "MergeFailedStep",
    "MergeState",
    "RecipeSource",
    "RestartScope",
    "RetryReason",
    "Severity",
    "SkillSource",
    "SubprocessResult",
    "SubprocessRunner",
    "T",
    "TerminationReason",
]
