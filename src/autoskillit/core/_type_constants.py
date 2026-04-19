"""Core constants for autoskillit.

Zero autoskillit imports. Provides the shared constant vocabulary for all higher layers.
"""

from __future__ import annotations

from importlib.metadata import version
from typing import NamedTuple

__all__ = [
    "AUTOSKILLIT_INSTALLED_VERSION",
    "AUTOSKILLIT_PRIVATE_ENV_VARS",
    "CONTEXT_EXHAUSTION_MARKER",
    "RESERVED_LOG_RECORD_KEYS",
    "PIPELINE_FORBIDDEN_TOOLS",
    "SKILL_TOOLS",
    "GATED_TOOLS",
    "HEADLESS_TOOLS",
    "FRANCHISE_TOOLS",
    "FREE_RANGE_TOOLS",
    "UNGATED_TOOLS",
    "PackDef",
    "PACK_REGISTRY",
    "RecipePackDef",
    "RECIPE_PACK_REGISTRY",
    "RECIPE_PACK_TAGS",
    "CATEGORY_TAGS",
    "TOOL_SUBSET_TAGS",
    "SKILL_COMMAND_PREFIX",
    "AUTOSKILLIT_SKILL_PREFIX",
    "RETIRED_READINESS_TOKENS",
    "SESSION_TYPE_ENV_VAR",
    "SESSION_TYPE_FRANCHISE",
    "SESSION_TYPE_ORCHESTRATOR",
    "SESSION_TYPE_LEAF",
    "HEADLESS_ENV_VAR",
]

AUTOSKILLIT_INSTALLED_VERSION: str = version("autoskillit")

# Session type environment variable and valid values.
# String aliases for consumers that cannot import SessionType StrEnum
# (hook scripts, shell wrappers, env builders).
SESSION_TYPE_ENV_VAR: str = "AUTOSKILLIT_SESSION_TYPE"
SESSION_TYPE_FRANCHISE: str = "franchise"
SESSION_TYPE_ORCHESTRATOR: str = "orchestrator"
SESSION_TYPE_LEAF: str = "leaf"
HEADLESS_ENV_VAR: str = "AUTOSKILLIT_HEADLESS"

# Env vars that control MCP server-level behavior and must not leak into
# user-code subprocesses (pytest runs, shell commands, etc.).
# Add new internal vars here as they are introduced.
AUTOSKILLIT_PRIVATE_ENV_VARS: frozenset[str] = frozenset(
    {
        "AUTOSKILLIT_HEADLESS",
        "AUTOSKILLIT_SKIP_STALE_CHECK",
        "AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK",
        "AUTOSKILLIT_FORCE_UPDATE_CHECK",
        # Franchise tier vars — must not leak into user-code subprocesses
        "AUTOSKILLIT_SESSION_TYPE",
        "AUTOSKILLIT_CAMPAIGN_ID",
        "AUTOSKILLIT_CAMPAIGN_STATE_PATH",
        "AUTOSKILLIT_PROJECT_DIR",
        "AUTOSKILLIT_L2_TOOL_TAGS",
    }
)

# The substring Claude CLI emits when the context window is full.
# Used by ClaudeSessionResult._is_context_exhausted() for detection.
# Centralized here so tests can reference the canonical value.
CONTEXT_EXHAUSTION_MARKER = "prompt is too long"

# Attribute names set unconditionally by logging.LogRecord.__init__ and makeRecord().
# Passing any of these as keys in the extra={} dict to ctx.info/ctx.error causes
# FastMCP's stdlib logging bridge to raise KeyError at runtime.
# Used by server/helpers._notify() for pre-dispatch validation.
RESERVED_LOG_RECORD_KEYS: frozenset[str] = frozenset(
    {
        # Set unconditionally in LogRecord.__init__
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        # Python 3.12+ addition
        "taskName",
        # Additional keys checked explicitly in makeRecord (not in __init__)
        "message",
        "asctime",
    }
)

# Native Claude Code tools that pipeline orchestrators must NEVER use directly.
# Canonical source of truth — imported by server.py and tests.
PIPELINE_FORBIDDEN_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "Edit",
    "Write",
    "Bash",
    "Agent",  # actual tool name; "Explore" is a subagent_type parameter, blocked via Agent
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
)

# Skill tools that route headless Claude sessions — canonical constant used by
# recipe_validator.py.
SKILL_TOOLS: frozenset[str] = frozenset({"run_skill"})

# Authoritative MCP tool registries. Defined here (L0) so both pipeline/ (L1)
# and recipe/ (L2) can reference them without cross-layer import violations.
GATED_TOOLS: frozenset[str] = frozenset(
    {
        "run_cmd",
        "run_python",
        "read_db",
        "run_skill",
        "merge_worktree",
        "reset_test_dir",
        "classify_fix",
        "reset_workspace",
        "migrate_recipe",
        "clone_repo",
        "remove_clone",
        "push_to_remote",
        "report_bug",
        "prepare_issue",
        "enrich_issues",
        "claim_issue",
        "release_issue",
        "wait_for_ci",
        "wait_for_merge_queue",
        "check_repo_merge_state",
        "toggle_auto_merge",
        "create_unique_branch",
        "write_telemetry_files",
        "get_pr_reviews",
        "bulk_close_issues",
        "check_pr_mergeable",
        "set_commit_status",
        # Formerly ungated — now kitchen-gated:
        "fetch_github_issue",
        "get_issue_title",
        "get_ci_status",
        "get_pipeline_report",
        "get_quota_events",
        "get_timing_summary",
        "get_token_summary",
        "kitchen_status",
        "list_recipes",
        "load_recipe",
        "validate_recipe",
        "register_clone_status",
        "batch_cleanup_clones",
    }
)

HEADLESS_TOOLS: frozenset[str] = frozenset({"test_check"})

FRANCHISE_TOOLS: frozenset[str] = frozenset(
    {
        "batch_cleanup_clones",
        "get_pipeline_report",
        "get_token_summary",
        "get_timing_summary",
        "get_quota_events",
    }
)

FREE_RANGE_TOOLS: frozenset[str] = frozenset(
    {"open_kitchen", "close_kitchen", "disable_quota_guard"}
)

UNGATED_TOOLS: frozenset[str] = FREE_RANGE_TOOLS


class PackDef(NamedTuple):
    """Definition of a named skill pack with default visibility state."""

    default_enabled: bool
    description: str


class RecipePackDef(NamedTuple):
    """Definition of a named recipe pack with default visibility state."""

    default_enabled: bool
    description: str


PACK_REGISTRY: dict[str, PackDef] = {
    "kitchen-core": PackDef(True, "Core kitchen orchestration tools"),
    "github": PackDef(True, "GitHub issue and PR tools"),
    "ci": PackDef(True, "CI polling and merge queue tools"),
    "clone": PackDef(True, "Clone-based run isolation tools"),
    "telemetry": PackDef(True, "Token, timing, and quota reporting"),
    "arch-lens": PackDef(True, "Architecture diagram lenses"),
    "audit": PackDef(True, "Codebase audit skills"),
    "research": PackDef(False, "Research recipe and experiment skills"),
    "exp-lens": PackDef(False, "Experimental design audit lenses"),
    "vis-lens": PackDef(False, "Visualization planning lenses"),
}

CATEGORY_TAGS: frozenset[str] = frozenset(PACK_REGISTRY.keys())

RECIPE_PACK_REGISTRY: dict[str, RecipePackDef] = {
    "implementation-family": RecipePackDef(True, "Implementation and refactoring recipes"),
    "research-family": RecipePackDef(False, "Research and exploration recipes"),
    "orchestration-family": RecipePackDef(True, "Campaign orchestration and automation"),
}

RECIPE_PACK_TAGS: frozenset[str] = frozenset(RECIPE_PACK_REGISTRY.keys())

# Maps each MCP tool name to its functional category subset tags.
# Mirrors the FastMCP @mcp.tool(tags=...) category assignments in the server layer.
# Tools with no functional category are absent from this map (empty intersection = no finding).
TOOL_SUBSET_TAGS: dict[str, frozenset[str]] = {
    # github
    "fetch_github_issue": frozenset({"github"}),
    "get_issue_title": frozenset({"github"}),
    "report_bug": frozenset({"github"}),
    "prepare_issue": frozenset({"github"}),
    "enrich_issues": frozenset({"github"}),
    "claim_issue": frozenset({"github"}),
    "release_issue": frozenset({"github"}),
    "get_pr_reviews": frozenset({"github"}),
    "bulk_close_issues": frozenset({"github"}),
    "check_pr_mergeable": frozenset({"github"}),
    "push_to_remote": frozenset({"github"}),
    "create_unique_branch": frozenset({"github"}),
    "set_commit_status": frozenset({"github"}),
    # ci
    "wait_for_ci": frozenset({"ci"}),
    "wait_for_merge_queue": frozenset({"ci"}),
    "check_repo_merge_state": frozenset({"ci"}),
    "toggle_auto_merge": frozenset({"ci"}),
    "get_ci_status": frozenset({"ci"}),
    # clone
    "clone_repo": frozenset({"clone"}),
    "remove_clone": frozenset({"clone"}),
    "register_clone_status": frozenset({"clone"}),
    "batch_cleanup_clones": frozenset({"clone"}),
    # kitchen-core — telemetry
    "get_token_summary": frozenset({"kitchen-core", "telemetry"}),
    "get_timing_summary": frozenset({"kitchen-core", "telemetry"}),
    "write_telemetry_files": frozenset({"kitchen-core", "telemetry"}),
    "get_quota_events": frozenset({"kitchen-core", "telemetry"}),
    # kitchen-core — execution
    "run_cmd": frozenset({"kitchen-core"}),
    "run_python": frozenset({"kitchen-core"}),
    "run_skill": frozenset({"kitchen-core"}),
    # kitchen-core — workspace
    "test_check": frozenset({"kitchen-core"}),
    "reset_test_dir": frozenset({"kitchen-core"}),
    "reset_workspace": frozenset({"kitchen-core"}),
    "classify_fix": frozenset({"kitchen-core"}),
    # kitchen-core — recipe
    "list_recipes": frozenset({"kitchen-core"}),
    "load_recipe": frozenset({"kitchen-core"}),
    "validate_recipe": frozenset({"kitchen-core"}),
    "migrate_recipe": frozenset({"kitchen-core"}),
    # kitchen-core — status
    "kitchen_status": frozenset({"kitchen-core"}),
    "read_db": frozenset({"kitchen-core"}),
    "get_pipeline_report": frozenset({"kitchen-core"}),
    # kitchen-core — git
    "merge_worktree": frozenset({"kitchen-core"}),
}

# Canonical prefix required for all skill_command values passed to run_skill.
# Enforced at the Claude Code hook boundary by skill_command_guard.py.
SKILL_COMMAND_PREFIX: str = "/"

# Canonical prefix for bundled autoskillit slash commands.
AUTOSKILLIT_SKILL_PREFIX: str = "/autoskillit:"

# Log message tokens that were once used as subprocess readiness sync primitives
# and have since been retired. Any logger call using these tokens as its first
# positional argument is a structural anti-pattern — the lifespan's try: block
# and the anyio signal receiver replaced them with a filesystem sentinel.
# Consumed by test_lifespan_readiness_structural.py (AST Assertion C).
RETIRED_READINESS_TOKENS: frozenset[str] = frozenset(
    {
        "lifespan_started",
        "sigterm_handler_ready",
    }
)
