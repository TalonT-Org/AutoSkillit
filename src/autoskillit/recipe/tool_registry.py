"""Canonical ToolDef registry — single source of truth for MCP tool parameters.

Replaces the hard-coded ``_TOOL_PARAMS`` map in ``rules_tools.py``. The registry
serves two consumers:

1. ``ToolDef.for_tool(tool_name)`` returns the frozen parameter set accepted by
   the named MCP tool. Bidirectional parity against live MCP handlers is asserted
   by ``tests/recipe/test_rules_tools.py::test_tool_registry_matches_mcp_handlers``.
2. ``FRAMEWORK_ONLY_EXCLUSIONS`` is the literal, reason-documented set of MCP
   tool names that are framework-internal (orchestrator primitives) and are not
   callable from recipes. The set is enumerated with explicit reasons so future
   contributors can audit, not infer, exclusion policy.

IL-2 module: depends on IL-0 ``autoskillit.core`` only. Recipe-layer rules import
``ToolDef.for_tool`` rather than reading the legacy ``_TOOL_PARAMS`` dict.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class ToolDef:
    """Canonical parameter set for a single MCP tool.

    ``params`` is the ordered tuple of accepted parameter names — order matters
    for tools that take positional args, although all current MCP tools are
    keyword-only. ``kind`` distinguishes recipe-callable tools from framework
    primitives (gated, ungated, headless).
    """

    name: str
    params: tuple[str, ...]
    kind: str = "recipe_callable"

    @property
    def param_set(self) -> frozenset[str]:
        return frozenset(self.params)


# --- Recipe-callable MCP tools ---
# Params mirror the live handler signatures in src/autoskillit/server/tools/.
# Handler introspection in tests/recipe/test_rules_tools.py enforces parity.

_RECIPE_TOOL_DEFS: tuple[ToolDef, ...] = (
    ToolDef(
        name="run_skill",
        params=(
            "skill_command",
            "cwd",
            "model",
            "step_name",
            "step_provider",
            "order_id",
            "stale_threshold",
            "idle_output_timeout",
            "output_dir",
            "resume_session_id",
        ),
    ),
    ToolDef(name="run_cmd", params=("cmd", "cwd", "timeout", "step_name")),
    ToolDef(name="run_python", params=("callable", "args", "timeout", "work_dir")),
    ToolDef(name="test_check", params=("worktree_path", "step_name")),
    ToolDef(name="merge_worktree", params=("worktree_path", "base_branch", "step_name")),
    ToolDef(name="reset_test_dir", params=("test_dir", "force", "step_name")),
    ToolDef(name="classify_fix", params=("worktree_path", "base_branch", "step_name")),
    ToolDef(name="reset_workspace", params=("test_dir",)),
    ToolDef(name="validate_recipe", params=("script_path",)),
    ToolDef(name="migrate_recipe", params=("name",)),
    ToolDef(name="load_recipe", params=("name", "overrides", "ingredients_only")),
    ToolDef(name="list_recipes", params=()),
    ToolDef(
        name="clone_repo",
        params=("source_dir", "run_name", "branch", "strategy", "remote_url", "step_name"),
    ),
    ToolDef(name="remove_clone", params=("clone_path", "keep", "step_name")),
    ToolDef(
        name="push_to_remote",
        params=("clone_path", "branch", "source_dir", "remote_url", "force", "step_name"),
    ),
    ToolDef(
        name="register_clone_status",
        params=("clone_path", "status", "registry_path", "step_name"),
    ),
    ToolDef(
        name="batch_cleanup_clones",
        params=("registry_path", "all_owners", "owner_filter", "step_name"),
    ),
    ToolDef(name="init_session", params=("recipe_name",)),
    ToolDef(name="dispatch_food_truck", params=("recipe_name", "overrides")),
    ToolDef(name="wait_for_ci", params=("run_id", "poll_interval", "timeout", "step_name")),
    ToolDef(name="fetch_github_issue", params=("issue_url", "step_name")),
    ToolDef(name="create_github_issue", params=("title", "body", "labels")),
    ToolDef(name="add_github_comment", params=("issue_url", "body")),
    ToolDef(name="merge_pull_request", params=("pr_url", "merge_method", "step_name")),
    ToolDef(name="poll_pr_status", params=("pr_url", "expected_state", "poll_interval")),
    ToolDef(name="rebase_pull_request", params=("pr_url", "base_branch", "step_name")),
    ToolDef(name="query_kitchen_status", params=()),
    ToolDef(name="close_kitchen", params=()),
)

_RECIPE_TOOL_MAP: Mapping[str, ToolDef] = {td.name: td for td in _RECIPE_TOOL_DEFS}


# --- Framework-only MCP tools ---
# These are MCP tools that are exposed by the server but are not callable from
# recipe steps (they are framework primitives). Each entry documents the reason
# so future contributors can audit, not infer, exclusion policy. Absence from
# current recipes is NOT an exclusion criterion.

FRAMEWORK_ONLY_EXCLUSIONS: Final[frozenset[str]] = frozenset(
    {
        # orchestrator lifecycle — opens the dispatch session, not a recipe primitive
        "open_kitchen",
        # orchestrator lifecycle — counterpart to open_kitchen
        "close_kitchen",
    }
)


def for_tool(tool_name: str) -> ToolDef | None:
    """Return the ToolDef for ``tool_name`` or None if unknown."""
    return _RECIPE_TOOL_MAP.get(tool_name)


def is_framework_only(tool_name: str) -> bool:
    """True if ``tool_name`` is an MCP tool that is not callable from recipes."""
    return tool_name in FRAMEWORK_ONLY_EXCLUSIONS


def all_recipe_tools() -> frozenset[str]:
    """Return the set of recipe-callable tool names."""
    return frozenset(_RECIPE_TOOL_MAP)


__all__ = [
    "FRAMEWORK_ONLY_EXCLUSIONS",
    "ToolDef",
    "all_recipe_tools",
    "for_tool",
    "is_framework_only",
]
