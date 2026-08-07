"""Semantic rules for MCP tool name validity."""

from __future__ import annotations

from collections import deque
from pathlib import PurePosixPath

from autoskillit.core import (
    SKILL_TOOLS,
    TOOL_REGISTRY,
    TOOL_SUBSET_TAGS,
    BindingFailureCode,
    Severity,
    get_logger,
    get_tool_def,
)
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._rule_helpers import _MAX_HOPS
from autoskillit.recipe.contracts import (
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

logger = get_logger(__name__)

_ALL_TOOLS: frozenset[str] = frozenset(TOOL_REGISTRY)

_RUN_PYTHON_PATH_LIKE_ARGS: frozenset[str] = frozenset(
    {"output_dir", "workspace", "diagnostics_log_dir", "investigation_path", "plan_path"}
)

# Registry of context variables that are captured upstream and MUST be forwarded
# to any tool step that accepts them. Each entry maps a tool name to the set of
# context-param names that the tool relies on. When a recipe captures one of
# these variables and a downstream step calls the corresponding tool, the step
# must reference the variable in its `with:` block — silently defaulting would
# change tool behavior (e.g., enqueue strategy on a repo where auto-merge is off).
_TOOL_CONTEXT_PARAMS: dict[str, frozenset[str]] = {
    "wait_for_merge_queue": frozenset({"auto_merge_available"}),
    "enqueue_pr": frozenset({"auto_merge_available"}),
}


@semantic_rule(
    name="context-param-not-forwarded",
    description=(
        "Tool step omits a context variable that the tool depends on — recipes must "
        "forward context parameters explicitly so the tool receives the upstream "
        "capture value (e.g., auto_merge_available)"
    ),
    severity=Severity.ERROR,
)
def _check_context_param_not_forwarded(ctx: ValidationContext) -> list[RuleFinding]:
    """Fire when a tool step omits a known context parameter that the tool depends on.

    The ``_TOOL_CONTEXT_PARAMS`` registry declares which tool-params must be
    forwarded when the corresponding context variable has been captured
    upstream. Without this rule, a step calling ``wait_for_merge_queue`` could
    silently omit ``auto_merge_available`` from its ``with:`` block, falling
    back to the tool's default (typically ``True``) — even on repos where the
    upstream ``check_repo_merge_state`` capture indicated auto-merge is
    disabled. The watcher's internal re-enqueue logic would then attempt
    ``enablePullRequestAutoMerge`` and fail. See PR #3901.
    """
    captured_context: set[str] = set()
    for step in ctx.recipe.steps.values():
        if step.capture:
            captured_context.update(step.capture.keys())
        if step.capture_list:
            captured_context.update(step.capture_list.keys())

    if not captured_context:
        return []

    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool is None or step.tool not in _TOOL_CONTEXT_PARAMS:
            continue
        required_params = _TOOL_CONTEXT_PARAMS[step.tool]
        for param in required_params:
            if param not in captured_context:
                continue
            with_value = str((step.with_args or {}).get(param, ""))
            if f"context.{param}" in with_value:
                continue
            findings.append(
                make_finding(
                    rule_name="context-param-not-forwarded",
                    step_name=step_name,
                    message=f"Step {step_name!r} calls tool {step.tool!r} but does not "
                    f"forward the upstream-captured context variable {param!r}. "
                    f"Add {param}: ${{{{ context.{param} }}}} to the with: block. "
                    f"Omitting this param causes the tool to use its default "
                    f"value, which may not match the captured context "
                    f"(e.g., auto_merge_available for repos with auto-merge disabled).",
                )
            )
    return findings


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
                make_finding(
                    rule_name="constant-step-with-args",
                    step_name=step_name,
                    message=f"step '{step_name}' is a constant step but has 'with' args "
                    f"({list(step.with_args.keys())}). "
                    f"constant steps have no tool to receive arguments.",
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
                make_finding(
                    rule_name="unknown-tool",
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
                make_finding(
                    rule_name="subset-disabled-tool",
                    step_name=step_name,
                    message=f"step '{step_name}': tool '{step.tool}' belongs to "
                    f"the disabled subset '{disabled_subset}'. Enable "
                    f"'{disabled_subset}' in .autoskillit/config.yaml "
                    f"subsets.disabled to use this tool.",
                )
            )
    return findings


@semantic_rule(
    name="dead-with-param",
    description="with: key does not match any known parameter of the step's tool",
    severity=Severity.ERROR,
)
def _check_dead_with_params(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool is None:
            continue
        invocation = ctx.binding_projection.for_step(step_name)
        if invocation is None:
            continue
        tool_def = get_tool_def(step.tool)
        known_params = sorted(tool_def.param_set) if tool_def is not None else []
        for failure in invocation.failures:
            if failure.code is not BindingFailureCode.UNKNOWN_TOOL_PARAMETER:
                continue
            findings.append(
                make_finding(
                    rule_name="dead-with-param",
                    step_name=step_name,
                    message=f"step '{step_name}': with key '{failure.name}' is not a known "
                    f"parameter of tool '{step.tool}'. "
                    f"Known parameters: {known_params}",
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
                    make_finding(
                        rule_name="rebase-then-push-requires-force",
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


@semantic_rule(
    name="release-issue-requires-disposition",
    description=(
        "release_issue must have fail_label or target_branch — close_issue alone is not a "
        "valid disposition because it bypasses staging logic on non-default branches"
    ),
    severity=Severity.ERROR,
)
def _check_release_issue_requires_disposition(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool is None or step.tool != "release_issue":
            continue
        has_fail_label = bool(step.with_args.get("fail_label"))
        has_target_branch = bool(step.with_args.get("target_branch"))
        if not has_fail_label and not has_target_branch:
            findings.append(
                make_finding(
                    rule_name="release-issue-requires-disposition",
                    step_name=step_name,
                    message=f"Step '{step_name}' calls release_issue without fail_label or "
                    f"target_branch. close_issue alone is not a valid disposition — "
                    f"without target_branch, the tool cannot determine whether to stage "
                    f"(non-default branch) or close (promotion target). Add target_branch "
                    f"for success/staging paths or fail_label for failure paths.",
                )
            )
    return findings


@semantic_rule(
    name="patch-token-summary-requires-scoping-key",
    description=(
        "run_python step calling patch_pr_token_summary should pass order_id "
        "or kitchen_id for correct multi-clone scoping"
    ),
    severity=Severity.WARNING,
)
def _check_patch_token_summary_scoping_key(ctx: ValidationContext) -> list[RuleFinding]:
    """Warn when a patch_pr_token_summary step does not pass a scoping key.

    patch_pr_token_summary accepts either order_id (canonical scoping for fleet
    sessions spanning multiple clone directories) or kitchen_id (cross-cwd
    fallback for standalone recipes run inside a kitchen). The function
    self-resolves kitchen_id from the on-disk hook config when callers don't
    pass it, so a missing key is non-fatal — this rule serves as a documentation
    guard alerting recipe authors to the scoping requirement.
    """
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        is_run_python = step.tool == "run_python" or step.python is not None
        if not is_run_python:
            continue
        callable_val = (
            step.with_args.get("callable", "")
            if step.tool == "run_python"
            else (step.python or "")
        )
        if "patch_pr_token_summary" not in str(callable_val):
            continue
        if "order_id" not in step.with_args and "kitchen_id" not in step.with_args:
            findings.append(
                make_finding(
                    rule_name="patch-token-summary-requires-scoping-key",
                    step_name=step_name,
                    message=f"step '{step_name}': patch_pr_token_summary call is missing "
                    f"a scoping key (order_id or kitchen_id) in with args. "
                    "Without one, the function falls back to cwd_filter which "
                    "silently excludes worktree-scoped sessions. "
                    "Add 'order_id' or 'kitchen_id' to suppress this warning.",
                )
            )
    return findings


@semantic_rule(
    name="mixed-cwd-without-scoping-key",
    description=(
        "patch_token_summary steps must declare order_id or kitchen_id when the "
        "recipe runs run_skill steps from multiple distinct cwd values — the "
        "default cwd_filter silently excludes worktree-scoped sessions."
    ),
    severity=Severity.WARNING,
)
def _check_mixed_cwd_without_scoping_key(ctx: ValidationContext) -> list[RuleFinding]:
    """Fire when a recipe mixes cwd values but lacks a scoping key on patch_token_summary.

    A recipe whose run_skill steps target multiple distinct cwd expressions
    (e.g., some use context.work_dir and some use context.worktree_path)
    produces a heterogeneous set of cwd values in the sessions.jsonl index.
    A patch_token_summary step without order_id or kitchen_id will then fall
    back to cwd_filter, silently excluding the worktree-scoped sessions.
    """
    findings: list[RuleFinding] = []
    cwd_values: set[str] = set()
    for step in ctx.recipe.steps.values():
        if step.tool != "run_skill":
            continue
        cwd_val = (step.with_args or {}).get("cwd", "").strip()
        if cwd_val:
            cwd_values.add(cwd_val)
    if len(cwd_values) <= 1:
        return findings

    for step_name, step in ctx.recipe.steps.items():
        is_run_python = step.tool == "run_python" or step.python is not None
        if not is_run_python:
            continue
        callable_val = (
            step.with_args.get("callable", "")
            if step.tool == "run_python"
            else (step.python or "")
        )
        if "patch_pr_token_summary" not in str(callable_val):
            continue
        if "order_id" in step.with_args or "kitchen_id" in step.with_args:
            continue
        findings.append(
            make_finding(
                rule_name="mixed-cwd-without-scoping-key",
                step_name=step_name,
                message=f"step '{step_name}': recipe mixes {len(cwd_values)} distinct cwd "
                f"values across run_skill steps, but patch_pr_token_summary has no "
                f"scoping key (order_id or kitchen_id). cwd_filter will silently "
                f"exclude worktree-scoped sessions. Add order_id or kitchen_id to "
                f"with_args.",
            )
        )
    return findings


@semantic_rule(
    name="push-after-edit-requires-force",
    description=(
        "push_to_remote steps reachable from a write-behavior skill "
        "(always or conditional) must have force='true'."
    ),
    severity=Severity.ERROR,
)
def _check_push_after_edit_requires_force(ctx: ValidationContext) -> list[RuleFinding]:
    """Fire when a push_to_remote step follows a write-behavior skill without force='true'.

    After a write-behavior skill applies and commits changes, the branch history has
    diverged from the remote. A non-force push will be rejected as non-fast-forward.
    """
    try:
        manifest = load_bundled_manifest()
    except Exception:
        logger.warning(
            "push-after-edit-requires-force: failed to load manifest; skipping",
            exc_info=True,
        )
        return []

    max_hops = _MAX_HOPS
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "push_to_remote":
            continue
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque(
            (p, 1) for p in ctx.predecessors.get(step_name, set())
        )
        found_write_skill: str | None = None
        while queue and found_write_skill is None:
            pred_name, depth = queue.popleft()
            if pred_name in visited or depth > max_hops:
                continue
            visited.add(pred_name)
            pred = ctx.recipe.steps.get(pred_name)
            if pred is None:
                continue
            if pred.tool in SKILL_TOOLS:
                skill_cmd = (pred.with_args or {}).get("skill_command", "")
                skill = resolve_skill_name(skill_cmd)
                if skill:
                    contract = get_skill_contract(skill, manifest)
                    if (
                        contract
                        and contract.write_behavior in ("always", "conditional")
                        and not contract.read_only
                    ):
                        found_write_skill = pred_name
                        break
            queue.extend((p, depth + 1) for p in ctx.predecessors.get(pred_name, set()))
        if found_write_skill is not None and (
            (step.with_args or {}).get("force", "").strip().lower() != "true"
        ):
            findings.append(
                make_finding(
                    rule_name="push-after-edit-requires-force",
                    step_name=step_name,
                    message=f"push_to_remote step '{step_name}' follows write-behavior skill "
                    f"step '{found_write_skill}' but is missing force='true'. "
                    "A write-behavior skill rewrites commit history — a force push "
                    "(--force-with-lease) is required to update the remote.",
                )
            )

    return findings


@semantic_rule(
    name="run-python-requires-work-dir",
    description="run_python steps with relative path-like args must include work_dir",
    severity=Severity.ERROR,
)
def _check_run_python_requires_work_dir(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        is_run_python = step.tool == "run_python" or step.python is not None
        if not is_run_python:
            continue
        with_args = step.with_args or {}
        has_relative_path_like = any(
            k in _RUN_PYTHON_PATH_LIKE_ARGS
            and isinstance(v, str)
            and v
            and "${{" not in v
            and not PurePosixPath(v).is_absolute()
            for k, v in with_args.items()
        )
        if has_relative_path_like and "work_dir" not in with_args:
            findings.append(
                make_finding(
                    rule_name="run-python-requires-work-dir",
                    step_name=step_name,
                    message=f"step '{step_name}' has relative path-like args "
                    "but no work_dir — add work_dir to anchor paths",
                )
            )
        work_dir_val = with_args.get("work_dir")
        if (
            isinstance(work_dir_val, str)
            and work_dir_val
            and "${{" not in work_dir_val
            and not PurePosixPath(work_dir_val).is_absolute()
        ):
            findings.append(
                make_finding(
                    rule_name="run-python-requires-work-dir",
                    step_name=step_name,
                    message=f"step '{step_name}' has work_dir='{work_dir_val}' "
                    "which is not absolute and not a template — "
                    "use an absolute path or template expression",
                )
            )
    return findings
