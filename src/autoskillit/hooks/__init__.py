"""Hook scripts (PreToolUse and PostToolUse) for AutoSkillit."""

# HOOK_REGISTRY population is triggered here, after every other module-level
# binding is in place. The eager ``HOOK_REGISTRY.extend(_build_hook_registry())``
# call that used to live at module scope in ``autoskillit.hook_registry`` was
# removed to break the import cycle through ``autoskillit.hooks.__init__``
# (which imports ``HOOK_REGISTRY`` from that package): ``_build_hook_registry``
# resolves ``EXEMPT_*`` from ``autoskillit.hooks``, which is partially loaded
# until this very module finishes its import sequence. By the time control
# reaches this block, every constant the population needs is bound on
# ``autoskillit.hooks``, so the call succeeds and ``HOOK_REGISTRY_HASH`` is
# recomputed against the populated list (the placeholder cached in
# ``hook_registry.__init__`` against the empty list is overwritten here).
import autoskillit.hook_registry as _hr
from autoskillit.core import (
    QUOTA_BUDGET_EXCEEDED_TRIGGER,
    QUOTA_GUARD_DENY_TRIGGER,
    QUOTA_POST_BUDGET_EXCEEDED_TRIGGER,
    QUOTA_POST_WARNING_TRIGGER,
)
from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    HookDef,
    generate_hooks_json,
)
from autoskillit.hooks._capture_artifacts import (
    CaptureStoreStats,
    CleanupBlocker,
    CleanupProgress,
    SweepBudgetSpec,
    capture_store_stats,
    reconcile_capture_store,
)
from autoskillit.hooks._command_classification import (
    _INTERPRETER_LINE_RE,
    _WRITE_APIS_RE,
    command_has_blocked_protected_path_read,
)
from autoskillit.hooks._exploration_request_record import (
    consume_exploration_request_record,
)
from autoskillit.hooks._github_mutation_analysis import analyze_github_mutations

# Re-export the shared stdlib-only hook constants at the package level so
# consumers can import them without going through the canonical submodule.
# ``_hook_constants`` itself remains the canonical authority and is still
# importable directly by standalone guard scripts (via ``_HOOKS_DIR`` bootstrap).
from autoskillit.hooks._hook_constants import (  # noqa: E402,F401
    DENY_REASON_BY_GUARD,
    DENY_TRIGGER_BY_GUARD,
    EXEMPT_SESSION_TYPES_BY_GUARD,
    EXEMPT_SKILLS_BY_GUARD,
    RISKY_GH_SUBCOMMANDS,
    RISKY_GIT_OPERATIONS,
)
from autoskillit.hooks.formatters._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS
from autoskillit.hooks.guards.branch_protection_guard import BRANCH_PROTECTION_DENY_TRIGGER
from autoskillit.hooks.guards.review_loop_gate import REVIEW_LOOP_DENY_TRIGGER
from autoskillit.hooks.guards.skill_orchestration_guard import SKILL_ORCHESTRATION_DENY_TRIGGER

_HOOK_REGISTRY_LIST = _hr.HOOK_REGISTRY
_HOOK_REGISTRY_LIST.extend(_hr._build_hook_registry())
_hr.HOOK_REGISTRY_HASH = _hr.compute_registry_hash(
    _HOOK_REGISTRY_LIST,
    _hr.RETIRED_SCRIPT_BASENAMES,
    _hr.LIFECYCLE_CONTRACTS,
)
del _HOOK_REGISTRY_LIST

__all__ = [
    "HOOK_REGISTRY",
    "HookDef",
    "BRANCH_PROTECTION_DENY_TRIGGER",
    "DENY_REASON_BY_GUARD",
    "DENY_TRIGGER_BY_GUARD",
    "EXEMPT_SESSION_TYPES_BY_GUARD",
    "EXEMPT_SKILLS_BY_GUARD",
    "RISKY_GH_SUBCOMMANDS",
    "RISKY_GIT_OPERATIONS",
    "SKILL_ORCHESTRATION_DENY_TRIGGER",
    "QUOTA_GUARD_DENY_TRIGGER",
    "QUOTA_BUDGET_EXCEEDED_TRIGGER",
    "QUOTA_POST_WARNING_TRIGGER",
    "QUOTA_POST_BUDGET_EXCEEDED_TRIGGER",
    "REVIEW_LOOP_DENY_TRIGGER",
    "CaptureStoreStats",
    "CleanupBlocker",
    "CleanupProgress",
    "SweepBudgetSpec",
    "_HOOK_CONFIG_PATH_COMPONENTS",
    "_INTERPRETER_LINE_RE",
    "_WRITE_APIS_RE",
    "analyze_github_mutations",
    "capture_store_stats",
    "command_has_blocked_protected_path_read",
    "consume_exploration_request_record",
    "generate_hooks_json",
    "reconcile_capture_store",
]
