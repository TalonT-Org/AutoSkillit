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
    "FREE_RANGE_TOOLS",
    "UNGATED_TOOLS",
    "PackDef",
    "PACK_REGISTRY",
    "CATEGORY_TAGS",
    "TOOL_SUBSET_TAGS",
    "TOOL_CATEGORIES",
    "SKILL_COMMAND_PREFIX",
    "AUTOSKILLIT_SKILL_PREFIX",
]

AUTOSKILLIT_INSTALLED_VERSION: str = version("autoskillit")

# Env vars that control MCP server-level behavior and must not leak into
# user-code subprocesses (pytest runs, shell commands, etc.).
# Add new internal vars here as they are introduced.
AUTOSKILLIT_PRIVATE_ENV_VARS: frozenset[str] = frozenset(
    {
        "AUTOSKILLIT_HEADLESS",
        "AUTOSKILLIT_SKIP_STALE_CHECK",
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

FREE_RANGE_TOOLS: frozenset[str] = frozenset({"open_kitchen", "close_kitchen"})

UNGATED_TOOLS: frozenset[str] = FREE_RANGE_TOOLS


class PackDef(NamedTuple):
    """Definition of a named skill pack with default visibility state."""

    default_enabled: bool
    description: str


PACK_REGISTRY: dict[str, PackDef] = {
    "github": PackDef(True, "GitHub issue and PR tools"),
    "ci": PackDef(True, "CI polling and merge queue tools"),
    "clone": PackDef(True, "Clone-based run isolation tools"),
    "telemetry": PackDef(True, "Token, timing, and quota reporting"),
    "arch-lens": PackDef(True, "Architecture diagram lenses"),
    "audit": PackDef(True, "Codebase audit skills"),
    "research": PackDef(False, "Research recipe and experiment skills"),
    "exp-lens": PackDef(False, "Experimental design audit lenses"),
}

CATEGORY_TAGS: frozenset[str] = frozenset(PACK_REGISTRY.keys())

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
    "toggle_auto_merge": frozenset({"ci"}),
    "get_ci_status": frozenset({"ci"}),
    # clone
    "clone_repo": frozenset({"clone"}),
    "remove_clone": frozenset({"clone"}),
    "register_clone_status": frozenset({"clone"}),
    "batch_cleanup_clones": frozenset({"clone"}),
    # telemetry
    "get_token_summary": frozenset({"telemetry"}),
    "get_timing_summary": frozenset({"telemetry"}),
    "write_telemetry_files": frozenset({"telemetry"}),
    "get_quota_events": frozenset({"telemetry"}),
}

# Categorized tool listing for the open_kitchen response.
# Each entry is (category_name, tuple_of_tool_names). Tool names must match the
# registered MCP tool names exactly.
TOOL_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Execution", ("run_cmd", "run_python", "run_skill")),
    ("Testing & Workspace", ("test_check", "reset_test_dir", "classify_fix", "reset_workspace")),
    (
        "Git Operations",
        ("merge_worktree", "create_unique_branch", "check_pr_mergeable", "set_commit_status"),
    ),
    ("Recipes", ("migrate_recipe", "list_recipes", "load_recipe", "validate_recipe")),
    (
        "Clone & Remote",
        (
            "clone_repo",
            "remove_clone",
            "push_to_remote",
            "register_clone_status",
            "batch_cleanup_clones",
        ),
    ),
    (
        "GitHub",
        (
            "fetch_github_issue",
            "get_issue_title",
            "get_ci_status",
            "report_bug",
            "prepare_issue",
            "enrich_issues",
            "claim_issue",
            "release_issue",
            "wait_for_ci",
            "wait_for_merge_queue",
            "toggle_auto_merge",
            "get_pr_reviews",
            "bulk_close_issues",
        ),
    ),
    (
        "Telemetry & Diagnostics",
        (
            "read_db",
            "write_telemetry_files",
            "kitchen_status",
            "get_pipeline_report",
            "get_token_summary",
            "get_timing_summary",
            "get_quota_events",
        ),
    ),
    ("Kitchen", ("open_kitchen", "close_kitchen")),
)

# Canonical prefix required for all skill_command values passed to run_skill.
# Enforced at the Claude Code hook boundary by skill_command_guard.py.
SKILL_COMMAND_PREFIX: str = "/"

# Canonical prefix for bundled autoskillit slash commands.
AUTOSKILLIT_SKILL_PREFIX: str = "/autoskillit:"
