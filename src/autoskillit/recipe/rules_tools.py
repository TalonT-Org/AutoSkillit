"""Semantic rules for MCP tool name validity."""

from __future__ import annotations

from autoskillit.core import GATED_TOOLS, HEADLESS_TOOLS, TOOL_SUBSET_TAGS, UNGATED_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_ALL_TOOLS: frozenset[str] = GATED_TOOLS | UNGATED_TOOLS | HEADLESS_TOOLS

# Known parameter signatures for MCP tools that accept `with:` args in recipes.
# Intentionally hardcoded — recipe validation runs without a live MCP server.
_TOOL_PARAMS: dict[str, frozenset[str]] = {
    # --- Execution tools ---
    "run_skill": frozenset(
        {
            "skill_command",
            "cwd",
            "model",
            "step_name",
            "order_id",
            "stale_threshold",
            "idle_output_timeout",
        }
    ),
    "run_cmd": frozenset({"cmd", "cwd", "timeout", "step_name"}),
    "run_python": frozenset({"callable", "args", "timeout"}),
    # --- Workspace tools ---
    "test_check": frozenset({"worktree_path", "step_name"}),
    "merge_worktree": frozenset({"worktree_path", "base_branch", "step_name"}),
    "reset_test_dir": frozenset({"test_dir", "force", "step_name"}),
    "classify_fix": frozenset({"worktree_path", "base_branch", "step_name"}),
    "reset_workspace": frozenset({"test_dir"}),
    # --- Recipe tools ---
    "validate_recipe": frozenset({"script_path"}),
    "migrate_recipe": frozenset({"name"}),
    "load_recipe": frozenset({"name", "overrides"}),
    "list_recipes": frozenset(),
    # --- Clone tools ---
    "clone_repo": frozenset(
        {
            "source_dir",
            "run_name",
            "branch",
            "strategy",
            "remote_url",
            "step_name",
        }
    ),
    "remove_clone": frozenset({"clone_path", "keep", "step_name"}),
    "push_to_remote": frozenset(
        {
            "clone_path",
            "branch",
            "source_dir",
            "remote_url",
            "force",
            "step_name",
        }
    ),
    "register_clone_status": frozenset(
        {
            "clone_path",
            "status",
            "registry_path",
            "step_name",
        }
    ),
    "batch_cleanup_clones": frozenset(
        {
            "registry_path",
            "all_owners",
            "owner_filter",
            "step_name",
        }
    ),
    # --- CI tools ---
    "wait_for_ci": frozenset(
        {
            "branch",
            "repo",
            "remote_url",
            "head_sha",
            "workflow",
            "event",
            "timeout_seconds",
            "cwd",
            "step_name",
        }
    ),
    "wait_for_merge_queue": frozenset(
        {
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
            "step_name",
        }
    ),
    "enqueue_pr": frozenset(
        {
            "pr_number",
            "target_branch",
            "cwd",
            "auto_merge_available",
            "repo",
            "remote_url",
            "step_name",
        }
    ),
    "get_ci_status": frozenset({"branch", "run_id", "repo", "workflow", "event", "cwd"}),
    "set_commit_status": frozenset(
        {
            "sha",
            "state",
            "context",
            "description",
            "target_url",
            "repo",
            "cwd",
        }
    ),
    # --- Git tools ---
    "create_unique_branch": frozenset(
        {
            "slug",
            "issue_number",
            "remote",
            "cwd",
            "base_branch_name",
            "step_name",
        }
    ),
    "check_pr_mergeable": frozenset({"pr_number", "cwd", "repo"}),
    # --- Integration tools ---
    "report_bug": frozenset(
        {
            "error_context",
            "cwd",
            "severity",
            "model",
            "step_name",
        }
    ),
    "prepare_issue": frozenset(
        {
            "title",
            "body",
            "repo",
            "labels",
            "dry_run",
            "split",
        }
    ),
    "enrich_issues": frozenset({"issue_number", "batch", "dry_run", "repo"}),
    "claim_issue": frozenset({"issue_url", "label", "allow_reentry"}),
    "release_issue": frozenset({"issue_url", "label", "target_branch", "staged_label"}),
    "fetch_github_issue": frozenset({"issue_url", "include_comments"}),
    "get_issue_title": frozenset({"issue_url"}),
    # --- Status tools ---
    "get_quota_events": frozenset({"n"}),
    "write_telemetry_files": frozenset({"output_dir"}),
    "get_pr_reviews": frozenset({"pr_number", "cwd", "repo"}),
    "bulk_close_issues": frozenset({"issue_numbers", "comment", "cwd"}),
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
def _check_unknown_tool(ctx: ValidationContext) -> list[RuleFinding]:
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


@semantic_rule(
    name="rebase-then-push-requires-force",
    description=(
        "push_to_remote step that follows a resolve-merge-conflicts step must have force='true'"
    ),
    severity=Severity.ERROR,
)
def _check_rebase_then_push_requires_force(ctx: ValidationContext) -> list[RuleFinding]:
    """Detect push_to_remote steps that follow resolve-merge-conflicts without force='true'.

    resolve-merge-conflicts rewrites commit SHAs via rebase. Without force-with-lease,
    the subsequent push will be rejected by the remote as a non-fast-forward update.
    """
    # Build a predecessor map by inverting the successor-based step_graph.
    predecessors: dict[str, set[str]] = {name: set() for name in ctx.step_graph}
    for pred, succs in ctx.step_graph.items():
        for succ in succs:
            if succ in predecessors:
                predecessors[succ].add(pred)

    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "push_to_remote":
            continue
        # Check if any predecessor is a run_skill step that invokes resolve-merge-conflicts.
        for pred_name in predecessors.get(step_name, set()):
            pred_step = ctx.recipe.steps.get(pred_name)
            if pred_step is None or pred_step.tool != "run_skill":
                continue
            skill_command = pred_step.with_args.get("skill_command", "")
            if "resolve-merge-conflicts" not in skill_command:
                continue
            # Found a rebase predecessor — check that this push step has force='true'.
            if step.with_args.get("force", "").strip().lower() != "true":
                findings.append(
                    RuleFinding(
                        rule="rebase-then-push-requires-force",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"push_to_remote step '{step_name}' follows resolve-merge-conflicts "
                            f"step '{pred_name}' but is missing 'force: true'. "
                            "Rebase rewrites commit SHAs — a non-fast-forward force push "
                            "(--force-with-lease) is required to update the remote."
                        ),
                    )
                )
                break  # one finding per push step is sufficient
    return findings
