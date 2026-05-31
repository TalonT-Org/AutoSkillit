"""Tool registries, pack registries, tool-to-tag mappings, visibility tags.

Zero autoskillit imports.
"""

from __future__ import annotations

from typing import NamedTuple

from ._type_enums import FleetErrorCode

__all__ = [
    "PIPELINE_FORBIDDEN_TOOLS",
    "SKILL_TOOLS",
    "GATED_TOOLS",
    "HEADLESS_TOOLS",
    "FLEET_TOOLS",
    "FLEET_DISPATCH_TOOLS",
    "FLEET_MENU_TOOLS",
    "FLEET_ERROR_CODES",
    "FREE_RANGE_TOOLS",
    "UNGATED_TOOLS",
    "PackDef",
    "PACK_REGISTRY",
    "CATEGORY_TAGS",
    "RecipePackDef",
    "RECIPE_PACK_REGISTRY",
    "RECIPE_PACK_TAGS",
    "AgentPackDef",
    "AGENT_PACK_REGISTRY",
    "CORE_PACKS",
    "TOOL_SUBSET_TAGS",
    "ALL_VISIBILITY_TAGS",
]

# Native Claude Code tools that pipeline orchestrators must NEVER use directly.
PIPELINE_FORBIDDEN_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "Edit",
    "Write",
    "Bash",
    "Agent",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
)

# Skill tools that route headless Claude sessions.
SKILL_TOOLS: frozenset[str] = frozenset({"run_skill"})

# Authoritative MCP tool registries.
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
        "bootstrap_clone",
        "claim_and_resolve_issue",
        "create_and_publish_branch",
        "record_pipeline_step",
    }
)

HEADLESS_TOOLS: frozenset[str] = frozenset({"test_check", "unlock_agent_pack"})

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

FLEET_MENU_TOOLS: tuple[str, ...] = ("dispatch_food_truck", "record_gate_dispatch")

FLEET_ERROR_CODES: frozenset[str] = frozenset(FleetErrorCode)

FREE_RANGE_TOOLS: frozenset[str] = frozenset(
    {
        "open_kitchen",
        "close_kitchen",
        "disable_quota_guard",
        "reload_session",
        "configure_fleet",
        "configure_order",
        "lock_ingredients",  # NEW (#3357)
    }
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
    "audit-pipeline": PackDef(False, "Audit pipeline internals (recipe-dispatched only)"),
}

CATEGORY_TAGS: frozenset[str] = frozenset(PACK_REGISTRY.keys())

RECIPE_PACK_REGISTRY: dict[str, RecipePackDef] = {
    "implementation-family": RecipePackDef(True, "Implementation and refactoring recipes"),
    "research-family": RecipePackDef(False, "Research and exploration recipes"),
    "orchestration-family": RecipePackDef(True, "Campaign orchestration and automation"),
}

RECIPE_PACK_TAGS: frozenset[str] = frozenset(RECIPE_PACK_REGISTRY.keys())


class AgentPackDef(NamedTuple):
    """Definition of a named agent pack with default visibility state."""

    default_enabled: bool
    description: str


AGENT_PACK_REGISTRY: dict[str, AgentPackDef] = {
    "plan-review": AgentPackDef(False, "Adversarial plan review agents for make-plan and rectify"),
}

if any(k != k.lower() for k in AGENT_PACK_REGISTRY):
    raise AssertionError(
        "AGENT_PACK_REGISTRY keys must be lowercase. "
        f"Offending: {sorted(k for k in AGENT_PACK_REGISTRY if k != k.lower())}"
    )

CORE_PACKS: frozenset[str] = frozenset({"github", "ci", "clone", "telemetry"})

if any(k != k.lower() for k in PACK_REGISTRY):
    raise AssertionError(
        "PACK_REGISTRY keys must be lowercase. "
        f"Offending: {sorted(k for k in PACK_REGISTRY if k != k.lower())}"
    )
if any(k != k.lower() for k in RECIPE_PACK_REGISTRY):
    raise AssertionError(
        "RECIPE_PACK_REGISTRY keys must be lowercase. "
        f"Offending: {sorted(k for k in RECIPE_PACK_REGISTRY if k != k.lower())}"
    )

# Maps each MCP tool name to its functional category subset tags.
TOOL_SUBSET_TAGS: dict[str, frozenset[str]] = {
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
    "claim_and_resolve_issue": frozenset({"github"}),
    "create_and_publish_branch": frozenset({"github"}),
    "wait_for_ci": frozenset({"ci"}),
    "wait_for_merge_queue": frozenset({"ci"}),
    "check_repo_merge_state": frozenset({"ci"}),
    "toggle_auto_merge": frozenset({"ci"}),
    "enqueue_pr": frozenset({"ci"}),
    "get_ci_status": frozenset({"ci"}),
    "clone_repo": frozenset({"clone"}),
    "remove_clone": frozenset({"clone"}),
    "register_clone_status": frozenset({"clone"}),
    "batch_cleanup_clones": frozenset({"clone", "fleet"}),
    "bootstrap_clone": frozenset({"clone"}),
    "get_token_summary": frozenset({"kitchen-core", "telemetry", "fleet"}),
    "get_timing_summary": frozenset({"kitchen-core", "telemetry", "fleet"}),
    "write_telemetry_files": frozenset({"kitchen-core", "telemetry"}),
    "get_quota_events": frozenset({"kitchen-core", "telemetry", "fleet"}),
    "analyze_tool_sequences": frozenset({"kitchen-core", "telemetry"}),
    "run_cmd": frozenset({"kitchen-core"}),
    "run_python": frozenset({"kitchen-core"}),
    "run_skill": frozenset({"kitchen-core"}),
    "test_check": frozenset({"kitchen-core"}),
    "reset_test_dir": frozenset({"kitchen-core"}),
    "reset_workspace": frozenset({"kitchen-core"}),
    "classify_fix": frozenset({"kitchen-core"}),
    "list_recipes": frozenset({"kitchen-core", "fleet-dispatch"}),
    "load_recipe": frozenset({"kitchen-core", "fleet-dispatch"}),
    "validate_recipe": frozenset({"kitchen-core"}),
    "migrate_recipe": frozenset({"kitchen-core"}),
    "kitchen_status": frozenset({"kitchen-core"}),
    "read_db": frozenset({"kitchen-core"}),
    "get_pipeline_report": frozenset({"kitchen-core", "fleet"}),
    "dispatch_food_truck": frozenset({"kitchen-core", "fleet"}),
    "record_gate_dispatch": frozenset({"kitchen-core", "fleet"}),
    "merge_worktree": frozenset({"kitchen-core"}),
    "unlock_agent_pack": frozenset({"kitchen-core"}),
    "record_pipeline_step": frozenset({"kitchen-core"}),
}

ALL_VISIBILITY_TAGS: frozenset[str] = frozenset(
    {"kitchen", "headless", "fleet", "fleet-dispatch", "kitchen-core", "plan-review"}
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
