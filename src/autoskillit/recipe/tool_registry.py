"""Canonical ToolDef registry — single source of truth for MCP tool parameters.

Replaces the hard-coded ``_TOOL_PARAMS`` map in ``rules_tools.py``. The registry
serves two consumers:

1. ``ToolDef.for_tool(tool_name)`` returns the frozen parameter set accepted by
   the named MCP tool. Bidirectional parity against live MCP handlers is asserted
   by ``tests/server/test_tool_registry_parity.py``.
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
# Handler introspection in tests/server/test_tool_registry_parity.py enforces
# bidirectional parity: every @mcp.tool() handler must have a ToolDef (or be
# framework-only), and every ToolDef must correspond to a live handler.

_RECIPE_TOOL_DEFS: tuple[ToolDef, ...] = (
    ToolDef(
        name="analyze_tool_sequences",
        params=("format", "min_count", "recipe", "top_n"),
    ),
    ToolDef(
        name="batch_cleanup_clones",
        params=("registry_path", "all_owners", "owner_filter", "step_name"),
    ),
    ToolDef(
        name="bootstrap_clone",
        params=(
            "base_branch",
            "branch",
            "remote_url",
            "run_name",
            "source_dir",
            "step_name",
            "strategy",
        ),
    ),
    ToolDef(
        name="bulk_close_issues",
        params=("issue_numbers", "comment", "cwd"),
    ),
    ToolDef(
        name="check_pr_mergeable",
        params=("pr_number", "cwd", "repo"),
    ),
    ToolDef(
        name="check_repo_merge_state",
        params=("branch", "cwd", "remote_url", "step_name", "base_branch"),
    ),
    ToolDef(
        name="claim_and_resolve_issue",
        params=("issue_url", "label", "allow_reentry"),
    ),
    ToolDef(
        name="claim_issue",
        params=("issue_url", "label", "allow_reentry"),
    ),
    ToolDef(
        name="classify_fix",
        params=("worktree_path", "base_branch", "step_name"),
    ),
    ToolDef(
        name="clone_repo",
        params=(
            "source_dir",
            "run_name",
            "branch",
            "strategy",
            "remote_url",
            "step_name",
        ),
    ),
    ToolDef(
        name="configure_fleet",
        params=(
            "max_concurrent_dispatches",
            "max_total_issues",
            "default_timeout_sec",
            "max_extension_seconds",
            "idle_output_timeout",
            "acquire_timeout_sec",
            "max_issues_per_food_truck",
            "enable_deadline_extension",
            "default_model",
            "inspector_model",
        ),
    ),
    ToolDef(
        name="configure_order",
        params=(
            "timeout",
            "stale_threshold",
            "idle_output_timeout",
            "max_suppression_seconds",
            "default_model",
        ),
    ),
    ToolDef(
        name="create_and_publish_branch",
        params=(
            "issue_number",
            "issue_slug",
            "remote_url",
            "run_name",
            "step_name",
            "work_dir",
        ),
    ),
    ToolDef(
        name="create_unique_branch",
        params=(
            "slug",
            "issue_number",
            "remote",
            "cwd",
            "base_branch_name",
            "step_name",
        ),
    ),
    ToolDef(
        name="dispatch_food_truck",
        params=(
            "recipe",
            "task",
            "caller_instructions",
            "dispatch_name",
            "backend",
            "ingredients",
            "capture",
            "skip_when",
            "timeout_sec",
            "idle_output_timeout",
            "resume_session_id",
            "resume_message",
            "resume_checkpoint",
            "prior_dispatch_id",
        ),
    ),
    ToolDef(
        name="enqueue_pr",
        params=(
            "pr_number",
            "target_branch",
            "cwd",
            "auto_merge_available",
            "repo",
            "remote_url",
            "step_name",
        ),
    ),
    ToolDef(
        name="enrich_issues",
        params=("issue_number", "batch", "dry_run", "repo"),
    ),
    ToolDef(
        name="fetch_github_issue",
        params=("issue_url", "include_comments"),
    ),
    ToolDef(
        name="get_ci_status",
        params=("branch", "run_id", "repo", "workflow", "event", "cwd"),
    ),
    ToolDef(
        name="get_issue_title",
        params=("issue_url",),
    ),
    ToolDef(
        name="get_pipeline_report",
        params=("clear",),
    ),
    ToolDef(
        name="get_pr_reviews",
        params=("pr_number", "cwd", "repo"),
    ),
    ToolDef(
        name="get_quota_events",
        params=("n",),
    ),
    ToolDef(
        name="get_timing_summary",
        params=("format", "order_id", "clear"),
    ),
    ToolDef(
        name="get_token_summary",
        params=("format", "order_id", "clear"),
    ),
    ToolDef(name="kitchen_status", params=()),
    ToolDef(name="list_recipes", params=()),
    ToolDef(
        name="load_recipe",
        params=("name", "overrides", "ingredients_only"),
    ),
    ToolDef(
        name="lock_ingredients",
        params=("locked", "pipeline_id", "unlock"),
    ),
    ToolDef(
        name="merge_worktree",
        params=("worktree_path", "base_branch", "step_name"),
    ),
    ToolDef(name="migrate_recipe", params=("name",)),
    ToolDef(
        name="prepare_issue",
        params=("title", "body", "repo", "labels", "dry_run", "split"),
    ),
    ToolDef(
        name="push_to_remote",
        params=(
            "clone_path",
            "branch",
            "source_dir",
            "remote_url",
            "force",
            "step_name",
        ),
    ),
    ToolDef(
        name="read_db",
        params=("query", "params", "db_path", "timeout"),
    ),
    ToolDef(
        name="record_gate_dispatch",
        params=("dispatch_name", "approved"),
    ),
    ToolDef(
        name="record_pipeline_step",
        params=("pipeline_id", "op", "dependencies"),
    ),
    ToolDef(
        name="register_clone_status",
        params=("clone_path", "status", "registry_path", "step_name"),
    ),
    ToolDef(
        name="release_issue",
        params=(
            "issue_url",
            "label",
            "target_branch",
            "staged_label",
            "fail_label",
            "close_issue",
        ),
    ),
    ToolDef(name="remove_clone", params=("clone_path", "keep", "step_name")),
    ToolDef(
        name="report_bug",
        params=("error_context", "cwd", "severity", "model", "step_name"),
    ),
    ToolDef(
        name="reset_dispatch",
        params=("dispatch_id", "destroy_artifacts", "reset_to", "force"),
    ),
    ToolDef(name="reset_test_dir", params=("test_dir", "force", "step_name")),
    ToolDef(name="reset_workspace", params=("test_dir",)),
    ToolDef(
        name="run_cmd",
        params=("cmd", "cwd", "timeout", "step_name"),
    ),
    ToolDef(
        name="run_python",
        params=("callable", "args", "timeout", "work_dir"),
    ),
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
    ToolDef(
        name="set_commit_status",
        params=(
            "sha",
            "state",
            "context",
            "description",
            "target_url",
            "repo",
            "cwd",
        ),
    ),
    ToolDef(
        name="test_check",
        params=("worktree_path", "step_name"),
    ),
    ToolDef(
        name="toggle_auto_merge",
        params=("pr_number", "target_branch", "cwd", "remote_url", "repo"),
    ),
    ToolDef(name="unlock_agent_pack", params=("pack_name",)),
    ToolDef(name="validate_recipe", params=("script_path",)),
    ToolDef(
        name="wait_for_ci",
        params=(
            "auto_trigger",
            "branch",
            "repo",
            "remote_url",
            "head_sha",
            "workflow",
            "event",
            "timeout_seconds",
            "lookback_seconds",
            "cwd",
            "step_name",
        ),
    ),
    ToolDef(
        name="wait_for_merge_queue",
        params=(
            "pr_number",
            "target_branch",
            "cwd",
            "repo",
            "remote_url",
            "timeout_seconds",
            "poll_interval",
            "stall_grace_period",
            "max_stall_retries",
            "not_in_queue_confirmation_cycles",
            "max_inconclusive_retries",
            "auto_merge_available",
            "max_merge_group_drops",
            "merge_group_drop_backoff",
            "step_name",
        ),
    ),
    ToolDef(name="write_telemetry_files", params=("output_dir",)),
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
        # framework admin — temporarily disables the API quota guard for the
        # current session; not a recipe primitive
        "disable_quota_guard",
        # framework session control — reloads the parent autoskillit process;
        # not a recipe primitive
        "reload_session",
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


def unsupported_params(tool_name: str, keys: frozenset[str] | set[str]) -> frozenset[str]:
    """Return the subset of ``keys`` not declared in the canonical ``ToolDef`` for ``tool_name``.

    The single helper consumed by delivery evidence, the dedicated ERROR-level
    ``unsupported-run-skill-param`` rule, and the WARNING-level
    ``dead-with-param`` rule — replacing the legacy duplicate
    ``rules_tools._TOOL_PARAMS`` lookup with the canonical registry.

    A tool unknown to the registry returns the entire ``keys`` set as
    unsupported; framework-only exclusions are always unsupported from a
    recipe step. Both branches are fail-closed by design — a missing registry
    entry never silently passes.
    """
    key_set = frozenset(keys)
    if tool_name in FRAMEWORK_ONLY_EXCLUSIONS:
        return key_set
    td = _RECIPE_TOOL_MAP.get(tool_name)
    if td is None:
        return key_set
    return frozenset(k for k in key_set if k not in td.param_set)


__all__ = [
    "FRAMEWORK_ONLY_EXCLUSIONS",
    "ToolDef",
    "all_recipe_tools",
    "for_tool",
    "is_framework_only",
    "unsupported_params",
]
