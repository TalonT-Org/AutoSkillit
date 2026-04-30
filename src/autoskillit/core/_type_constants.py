"""Core constants for autoskillit.

Zero autoskillit imports. Provides the shared constant vocabulary for all higher layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from importlib.metadata import version
from typing import NamedTuple

from ._type_enums import FeatureLifecycle, FleetErrorCode

__all__ = [
    "AUTOSKILLIT_INSTALLED_VERSION",
    "AUTOSKILLIT_PRIVATE_ENV_VARS",
    "CONTEXT_EXHAUSTION_MARKER",
    "RESERVED_LOG_RECORD_KEYS",
    "PIPELINE_FORBIDDEN_TOOLS",
    "SKILL_TOOLS",
    "GATED_TOOLS",
    "HEADLESS_TOOLS",
    "FLEET_TOOLS",
    "FLEET_MENU_TOOLS",
    "FREE_RANGE_TOOLS",
    "UNGATED_TOOLS",
    "PackDef",
    "PACK_REGISTRY",
    "RecipePackDef",
    "RECIPE_PACK_REGISTRY",
    "RECIPE_PACK_TAGS",
    "CORE_PACKS",
    "CATEGORY_TAGS",
    "TOOL_SUBSET_TAGS",
    "ALL_VISIBILITY_TAGS",
    "SKILL_COMMAND_PREFIX",
    "AUTOSKILLIT_SKILL_PREFIX",
    "RETIRED_READINESS_TOKENS",
    "SESSION_TYPE_ENV_VAR",
    "SESSION_TYPE_FLEET",
    "SESSION_TYPE_ORCHESTRATOR",
    "SESSION_TYPE_LEAF",
    "SESSION_TYPE_COOK",
    "SESSION_TYPE_ORDER",
    "HEADLESS_ENV_VAR",
    "FLEET_MODE_ENV_VAR",
    "FLEET_DISPATCH_MODE",
    "CAMPAIGN_ID_ENV_VAR",
    "DISPATCH_ID_ENV_VAR",
    "KITCHEN_SESSION_ID_ENV_VAR",
    "LAUNCH_ID_ENV_VAR",
    "FLEET_DISPATCH_TOOLS",
    "FLEET_ERROR_CODES",
    "FeatureDef",
    "FEATURE_REGISTRY",
    "RETIRED_FEATURES",
    "SKILL_FILE_ADVISORY_MAP",
    "SKILL_ACTIVATE_DEPS_REQUIRED",
    "SOUS_CHEF_MANDATORY_SECTIONS",
    "SOUS_CHEF_L2_SECTIONS",
]

AUTOSKILLIT_INSTALLED_VERSION: str = version("autoskillit")

# Session type environment variable and valid values.
# String aliases for consumers that cannot import SessionType StrEnum
# (hook scripts, shell wrappers, env builders).
SESSION_TYPE_ENV_VAR: str = "AUTOSKILLIT_SESSION_TYPE"
SESSION_TYPE_ORCHESTRATOR: str = "orchestrator"
SESSION_TYPE_FLEET: str = "fleet"
SESSION_TYPE_LEAF: str = "leaf"
SESSION_TYPE_COOK: str = "cook"
SESSION_TYPE_ORDER: str = "order"
HEADLESS_ENV_VAR: str = "AUTOSKILLIT_HEADLESS"
CAMPAIGN_ID_ENV_VAR: str = "AUTOSKILLIT_CAMPAIGN_ID"
FLEET_MODE_ENV_VAR: str = "AUTOSKILLIT_FLEET_MODE"
FLEET_DISPATCH_MODE: str = "dispatch"
DISPATCH_ID_ENV_VAR: str = "AUTOSKILLIT_DISPATCH_ID"
KITCHEN_SESSION_ID_ENV_VAR: str = "AUTOSKILLIT_KITCHEN_SESSION_ID"
LAUNCH_ID_ENV_VAR: str = "AUTOSKILLIT_LAUNCH_ID"

# Env vars that control MCP server-level behavior and must not leak into
# user-code subprocesses (pytest runs, shell commands, etc.).
# Add new internal vars here as they are introduced.
AUTOSKILLIT_PRIVATE_ENV_VARS: frozenset[str] = frozenset(
    {
        "AUTOSKILLIT_HEADLESS",
        "AUTOSKILLIT_SKIP_STALE_CHECK",
        "AUTOSKILLIT_SKIP_UPDATE_CHECK",
        "AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK",
        "AUTOSKILLIT_FORCE_UPDATE_CHECK",
        # Fleet tier vars — must not leak into user-code subprocesses
        "AUTOSKILLIT_SESSION_TYPE",
        "AUTOSKILLIT_FLEET_MODE",
        "AUTOSKILLIT_CAMPAIGN_ID",
        "AUTOSKILLIT_DISPATCH_ID",
        "AUTOSKILLIT_KITCHEN_SESSION_ID",
        "AUTOSKILLIT_CAMPAIGN_STATE_PATH",
        "AUTOSKILLIT_PROJECT_DIR",
        "AUTOSKILLIT_L2_TOOL_TAGS",
        "AUTOSKILLIT_LAUNCH_ID",
    }
)

# The substring Claude CLI emits when the context window is full.
# Used by ClaudeSessionResult._is_context_exhausted() for detection.
# Centralized here so tests can reference the canonical value.
CONTEXT_EXHAUSTION_MARKER = "prompt is too long"

# Attribute names set unconditionally by logging.LogRecord.__init__ and makeRecord().
# Passing any of these as keys in the extra={} dict to ctx.info/ctx.error causes
# FastMCP's stdlib logging bridge to raise KeyError at runtime.
# Used by server/_notify._notify() for pre-dispatch validation.
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
        "enqueue_pr",
        "create_unique_branch",
        "write_telemetry_files",
        "get_pr_reviews",
        "bulk_close_issues",
        "check_pr_mergeable",
        "set_commit_status",
        "analyze_tool_sequences",
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
        "dispatch_food_truck",
        "record_gate_dispatch",
    }
)

HEADLESS_TOOLS: frozenset[str] = frozenset({"test_check"})

FLEET_TOOLS: frozenset[str] = frozenset(
    {
        "batch_cleanup_clones",
        "get_pipeline_report",
        "get_token_summary",
        "get_timing_summary",
        "get_quota_events",
        "dispatch_food_truck",
        "record_gate_dispatch",
    }
)

FLEET_DISPATCH_TOOLS: frozenset[str] = frozenset(
    {
        "list_recipes",
        "load_recipe",
        "fetch_github_issue",
        "get_issue_title",
    }
)

# Tools that appear in the Fleet group in the cook menu and open_kitchen response.
# Defined here (L0) so menu modules can import the constant without loading the fleet package.
FLEET_MENU_TOOLS: tuple[str, ...] = ("dispatch_food_truck", "record_gate_dispatch")

FLEET_ERROR_CODES: frozenset[str] = frozenset(FleetErrorCode)

FREE_RANGE_TOOLS: frozenset[str] = frozenset(
    {"open_kitchen", "close_kitchen", "disable_quota_guard", "reload_session"}
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

CORE_PACKS: frozenset[str] = frozenset({"github", "ci", "clone", "telemetry"})

# Maps each MCP tool name to its functional category subset tags.
# Mirrors the FastMCP @mcp.tool(tags=...) category assignments in the server layer.
# Tools with no functional category are absent from this map (empty intersection = no finding).
TOOL_SUBSET_TAGS: dict[str, frozenset[str]] = {
    # github
    "fetch_github_issue": frozenset({"github", "fleet-dispatch"}),
    "get_issue_title": frozenset({"github", "fleet-dispatch"}),
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
    "enqueue_pr": frozenset({"ci"}),
    "get_ci_status": frozenset({"ci"}),
    # clone
    "clone_repo": frozenset({"clone"}),
    "remove_clone": frozenset({"clone"}),
    "register_clone_status": frozenset({"clone"}),
    "batch_cleanup_clones": frozenset({"clone", "fleet"}),
    # kitchen-core — telemetry
    "get_token_summary": frozenset({"kitchen-core", "telemetry", "fleet"}),
    "get_timing_summary": frozenset({"kitchen-core", "telemetry", "fleet"}),
    "write_telemetry_files": frozenset({"kitchen-core", "telemetry"}),
    "get_quota_events": frozenset({"kitchen-core", "telemetry", "fleet"}),
    "analyze_tool_sequences": frozenset({"kitchen-core", "telemetry"}),
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
    "list_recipes": frozenset({"kitchen-core", "fleet-dispatch"}),
    "load_recipe": frozenset({"kitchen-core", "fleet-dispatch"}),
    "validate_recipe": frozenset({"kitchen-core"}),
    "migrate_recipe": frozenset({"kitchen-core"}),
    # kitchen-core — status
    "kitchen_status": frozenset({"kitchen-core"}),
    "read_db": frozenset({"kitchen-core"}),
    "get_pipeline_report": frozenset({"kitchen-core", "fleet"}),
    "dispatch_food_truck": frozenset({"kitchen-core", "fleet"}),
    "record_gate_dispatch": frozenset({"kitchen-core", "fleet"}),
    # kitchen-core — git
    "merge_worktree": frozenset({"kitchen-core"}),
}

ALL_VISIBILITY_TAGS: frozenset[str] = frozenset(
    {"kitchen", "headless", "fleet", "fleet-dispatch", "kitchen-core"}
)

if not TOOL_SUBSET_TAGS:
    raise RuntimeError("TOOL_SUBSET_TAGS is empty — cannot validate ALL_VISIBILITY_TAGS coverage")
_all_tool_tags = {tag for tags in TOOL_SUBSET_TAGS.values() for tag in tags}
_non_category_tool_tags = _all_tool_tags - CATEGORY_TAGS
if not _non_category_tool_tags <= ALL_VISIBILITY_TAGS:
    _missing = _non_category_tool_tags - ALL_VISIBILITY_TAGS
    raise RuntimeError(
        f"ALL_VISIBILITY_TAGS is missing non-category tags found in TOOL_SUBSET_TAGS: "
        f"{sorted(_missing)}. Add the missing tags to ALL_VISIBILITY_TAGS."
    )


@dataclass(frozen=True)
class FeatureDef:
    """Definition of a named feature gate."""

    lifecycle: FeatureLifecycle
    description: str
    tool_tags: frozenset[str]
    skill_categories: frozenset[str]
    import_package: str | None
    tier: int = 1
    default_enabled: bool = False
    depends_on: frozenset[str] = field(default_factory=frozenset)
    since_version: str | None = None
    sunset_date: date | None = None


FEATURE_REGISTRY: dict[str, FeatureDef] = {
    "fleet": FeatureDef(
        lifecycle=FeatureLifecycle.EXPERIMENTAL,
        description="L3 Fleet Orchestrator — multi-session campaign dispatch",
        tool_tags=frozenset({"fleet"}),
        skill_categories=frozenset({"fleet"}),
        import_package="autoskillit.fleet",
        tier=1,
        default_enabled=False,
        since_version="0.9.119",
    ),
    "planner": FeatureDef(
        lifecycle=FeatureLifecycle.EXPERIMENTAL,
        description=(
            "Progressive resolution planner — 3-pass sequential decomposition"
            " into GitHub-issue-ready work packages"
        ),
        tool_tags=frozenset(),
        skill_categories=frozenset({"planner"}),
        import_package="autoskillit.planner",
        tier=1,
        default_enabled=False,
        since_version="0.9.119",
    ),
}

RETIRED_FEATURES: frozenset[str] = frozenset()

# Guard: FeatureDef.tool_tags must be in TOOL_SUBSET_TAGS — checked at import time.
_ALL_REGISTERED_TOOL_TAGS: frozenset[str] = frozenset(
    tag for tags in TOOL_SUBSET_TAGS.values() for tag in tags
)
if not all(
    tag in _ALL_REGISTERED_TOOL_TAGS
    for defn in FEATURE_REGISTRY.values()
    for tag in defn.tool_tags
):
    raise AssertionError(
        "FeatureDef.tool_tags contains a tag not present in TOOL_SUBSET_TAGS values. "
        "Add the tag to the appropriate tool entry in TOOL_SUBSET_TAGS first."
    )


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

# Maps file-path regex patterns to the advisory skill name to suggest when that
# path is written or edited. Patterns are tried in order; first match wins.
# Campaign paths must appear before the general recipe pattern.
# Stdlib-only hooks inline a copy of the recipe-related subset; the contract
# test test_hook_patterns_match_type_constants asserts they stay in sync.
SKILL_FILE_ADVISORY_MAP: dict[str, str] = {
    r"(?:\.autoskillit|src/autoskillit)/recipes/campaigns/.*\.ya?ml$": "make-campaign",
    r"(?:\.autoskillit|src/autoskillit)/recipes/.*\.ya?ml$": "write-recipe",
}

# Pipeline skills that must declare specific activate_deps. Contract test
# test_required_activate_deps_present enforces this invariant at CI time.
SKILL_ACTIVATE_DEPS_REQUIRED: dict[str, frozenset[str]] = {
    "make-plan": frozenset({"arch-lens", "write-recipe"}),
    "implement-worktree": frozenset({"write-recipe"}),
    "implement-worktree-no-merge": frozenset({"write-recipe"}),
}

# Single registration point: adding a section here surfaces any path that fails to deliver it.
SOUS_CHEF_MANDATORY_SECTIONS: tuple[str, ...] = (
    "MULTI-PART PLAN SEQUENCING",
    "SKILL_COMMAND FORMATTING",
    "CONTEXT LIMIT ROUTING",
    "AUDIT-IMPL ACROSS MULTI-GROUP PIPELINES",
    "READING AND ACTING ON `plan_parts=` OUTPUT",
    "MULTIPLE ISSUES",
    "PARALLEL STEP SCHEDULING",
    "EXECUTION MAP — GROUP DISPATCH",
    "STEP NAME IMMUTABILITY",
    "MERGE PHASE",
    "QUOTA WAIT PROTOCOL",
    "STEP EXECUTION IS NOT DISCRETIONARY",
    "NARRATION SUPPRESSION",
)

# Strict subset of SOUS_CHEF_MANDATORY_SECTIONS delivered to L2 food truck sessions.
SOUS_CHEF_L2_SECTIONS: tuple[str, ...] = (
    "CONTEXT LIMIT ROUTING",
    "STEP NAME IMMUTABILITY",
    "MERGE PHASE",
    "QUOTA WAIT PROTOCOL",
)
assert set(SOUS_CHEF_L2_SECTIONS).issubset(set(SOUS_CHEF_MANDATORY_SECTIONS))
