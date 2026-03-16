"""Semantic rules for MCP tool name validity."""

from __future__ import annotations

from autoskillit.core import GATED_TOOLS, HEADLESS_TOOLS, TOOL_SUBSET_TAGS, UNGATED_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_ALL_TOOLS: frozenset[str] = GATED_TOOLS | UNGATED_TOOLS | HEADLESS_TOOLS

# Known parameter signatures for MCP tools that accept `with:` args in recipes.
# Intentionally hardcoded — recipe validation runs without a live MCP server.
_TOOL_PARAMS: dict[str, frozenset[str]] = {
    "run_skill": frozenset({"skill_command", "cwd", "model", "step_name"}),
    "run_cmd": frozenset({"command", "cwd", "timeout", "step_name"}),
    "run_python": frozenset({"callable_path", "kwargs", "step_name"}),
    "test_check": frozenset({"worktree_path"}),
    "merge_worktree": frozenset({"worktree_path", "base_branch"}),
    "reset_test_dir": frozenset({"test_dir", "force"}),
    "classify_fix": frozenset({"worktree_path", "base_branch"}),
    "reset_workspace": frozenset({"test_dir"}),
    "validate_recipe": frozenset({"script_path"}),
    "clone_repo": frozenset({"repo", "branch", "target_dir"}),
    "remove_clone": frozenset({"clone_dir", "keep"}),
    "push_to_remote": frozenset({"clone_dir", "branch"}),
    "report_bug": frozenset({"error_context", "report_path", "cwd"}),
    "prepare_issue": frozenset({"title", "body", "labels", "cwd"}),
    "enrich_issues": frozenset({"cwd"}),
    "claim_issue": frozenset({"issue_number", "cwd"}),
    "release_issue": frozenset({"issue_number", "cwd"}),
    "wait_for_ci": frozenset({"branch", "repo", "cwd", "timeout_seconds", "poll_interval"}),
    "wait_for_merge_queue": frozenset(
        {"pr_number", "target_branch", "repo", "cwd", "timeout_seconds", "poll_interval"}
    ),
    "create_unique_branch": frozenset({"base_name", "cwd"}),
    "check_pr_mergeable": frozenset({"pr_number", "cwd"}),
    "write_telemetry_files": frozenset({"output_dir"}),
    "get_pr_reviews": frozenset({"pr_number", "cwd"}),
    "bulk_close_issues": frozenset({"issue_numbers", "comment", "cwd"}),
    "set_commit_status": frozenset(
        {"sha", "state", "context", "description", "target_url", "cwd"}
    ),
    "get_quota_events": frozenset({"minutes"}),
    "migrate_recipe": frozenset({"recipe_path"}),
    "load_recipe": frozenset({"recipe_name", "ingredients", "cwd"}),
    "list_recipes": frozenset({"cwd"}),
    "fetch_github_issue": frozenset({"issue_number", "repo", "cwd"}),
    "get_issue_title": frozenset({"issue_number", "repo", "cwd"}),
    "get_ci_status": frozenset({"branch", "repo", "cwd"}),
}


@semantic_rule(
    name="constant-step-with-args",
    description="constant step must not have with args — there is no tool to receive them",
    severity=Severity.ERROR,
)
def _check_constant_step_no_with_args(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.constant is not None and step.with_args:
            findings.append(
                RuleFinding(
                    rule="constant-step-with-args",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"step '{step_name}' is a constant step but has 'with' args "
                        f"({list(step.with_args.keys())}). "
                        f"constant steps have no tool to receive arguments."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="unknown-tool",
    description="step.tool must be a registered MCP tool name",
    severity=Severity.ERROR,
)
def _unknown_tool(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool is None:
            continue
        if step.tool not in _ALL_TOOLS:
            findings.append(
                RuleFinding(
                    rule="unknown-tool",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"step '{step_name}': tool '{step.tool}' is not a registered MCP tool. "
                        f"Known tools: {sorted(_ALL_TOOLS)}"
                    ),
                )
            )
    return findings


@semantic_rule(
    name="subset-disabled-tool",
    description=(
        "step.tool belongs to a functional category currently disabled in subsets.disabled config"
    ),
    severity=Severity.WARNING,
)
def _check_subset_disabled_tool(ctx: ValidationContext) -> list[RuleFinding]:
    if not ctx.disabled_subsets:
        return []
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool is None or step.tool not in _ALL_TOOLS:
            continue
        tool_categories = TOOL_SUBSET_TAGS.get(step.tool, frozenset())
        overlap = tool_categories & ctx.disabled_subsets
        if overlap:
            disabled_subset = next(iter(sorted(overlap)))
            findings.append(
                RuleFinding(
                    rule="subset-disabled-tool",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"step '{step_name}': tool '{step.tool}' belongs to "
                        f"the disabled subset '{disabled_subset}'. Enable "
                        f"'{disabled_subset}' in .autoskillit/config.yaml "
                        f"subsets.disabled to use this tool."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="dead-with-param",
    description="with: key does not match any known parameter of the step's tool",
    severity=Severity.WARNING,
)
def _check_dead_with_params(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool is None or step.tool not in _TOOL_PARAMS:
            continue
        known_params = _TOOL_PARAMS[step.tool]
        for key in step.with_args:
            if key not in known_params:
                findings.append(
                    RuleFinding(
                        rule="dead-with-param",
                        severity=Severity.WARNING,
                        step_name=step_name,
                        message=(
                            f"step '{step_name}': with key '{key}' is not a known "
                            f"parameter of tool '{step.tool}'. "
                            f"Known parameters: {sorted(known_params)}"
                        ),
                    )
                )
    return findings
