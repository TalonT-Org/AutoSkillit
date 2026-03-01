"""Semantic validation rules for the recipe schema."""

from __future__ import annotations

from autoskillit.core import (
    AUTOSKILLIT_INSTALLED_VERSION,
    PIPELINE_FORBIDDEN_TOOLS,
    SKILL_TOOLS,
    Severity,
    get_logger,
)
from autoskillit.recipe.contracts import (
    _CONTEXT_REF_RE,
    _RESULT_CAPTURE_RE,
    count_positional_args,
    extract_context_refs,
    extract_input_refs,
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.recipe.io import iter_steps_with_context
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import Recipe

logger = get_logger(__name__)

_WORKTREE_CREATING_SKILLS = frozenset(
    {
        "implement-worktree",
        "implement-worktree-no-merge",
    }
)


@semantic_rule(
    name="outdated-recipe-version",
    description="Recipe's autoskillit_version is below the installed package version",
    severity=Severity.WARNING,
)
def _check_outdated_version(wf: Recipe) -> list[RuleFinding]:
    from packaging.version import Version

    script_ver = wf.version
    if script_ver is None:
        return [
            RuleFinding(
                rule="outdated-recipe-version",
                severity=Severity.WARNING,
                step_name="(top-level)",
                message=(
                    f"Recipe has no autoskillit_version field. "
                    f"Current installed version is {AUTOSKILLIT_INSTALLED_VERSION}. "
                    f"Run 'autoskillit migrate' to update."
                ),
            )
        ]

    if Version(script_ver) < Version(AUTOSKILLIT_INSTALLED_VERSION):
        return [
            RuleFinding(
                rule="outdated-recipe-version",
                severity=Severity.WARNING,
                step_name="(top-level)",
                message=(
                    f"Recipe version {script_ver} is behind installed "
                    f"version {AUTOSKILLIT_INSTALLED_VERSION}."
                    " Run 'autoskillit migrate' to update."
                ),
            )
        ]

    return []


@semantic_rule(
    name="missing-ingredient",
    description="Skill steps must provide all required ingredients via context or recipe inputs.",
    severity=Severity.ERROR,
)
def _check_unsatisfied_skill_input(wf: Recipe) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()
    ingredient_names = set(wf.ingredients.keys())

    for step_name, step, available_context in iter_steps_with_context(wf):
        if step.tool in SKILL_TOOLS:
            skill_cmd = step.with_args.get("skill_command", "")
            skill_name = resolve_skill_name(skill_cmd)
            if skill_name:
                contract = get_skill_contract(skill_name, manifest)
                if contract:
                    # If the skill command has inline positional args beyond
                    # the skill name (e.g., "/autoskillit:investigate the
                    # test failures"), we cannot determine which named contract
                    # inputs they satisfy. Skip checking — only check steps
                    # that use explicit ${{ }} references for all arguments.
                    if count_positional_args(skill_cmd) > 0:
                        continue

                    ctx_refs = extract_context_refs(step)
                    inp_refs = extract_input_refs(step)
                    provided = ctx_refs | inp_refs

                    for req_input in contract.inputs:
                        if not req_input.required:
                            continue
                        name = req_input.name
                        if name not in provided:
                            if name in available_context or name in ingredient_names:
                                msg = (
                                    f"Step '{step_name}' invokes {skill_name} which requires "
                                    f"'{name}', and '{name}' is available in the recipe "
                                    f"context, but the step does not reference it. Add "
                                    f"'${{{{ context.{name} }}}}' to the step's skill_command "
                                    f"or with: block."
                                )
                            else:
                                msg = (
                                    f"Step '{step_name}' invokes {skill_name} which requires "
                                    f"'{name}', but '{name}' is not available at this point "
                                    f"in the recipe. No prior step captures it and it is "
                                    f"not a recipe ingredient."
                                )
                            findings.append(
                                RuleFinding(
                                    rule="missing-ingredient",
                                    severity=Severity.ERROR,
                                    step_name=step_name,
                                    message=msg,
                                )
                            )

    return findings


@semantic_rule(
    name="unreachable-step",
    description="Steps that no other step routes to (and are not the entry point) are dead code.",
    severity=Severity.WARNING,
)
def _check_unreachable_steps(wf: Recipe) -> list[RuleFinding]:
    if not wf.steps:
        return []

    referenced: set[str] = set()
    for step in wf.steps.values():
        for field in ("on_success", "on_failure", "on_retry"):
            target = getattr(step, field, None)
            if target:
                referenced.add(target)
        if step.on_result:
            referenced.update(step.on_result.routes.values())
        if step.retry and step.retry.on_exhausted:
            referenced.add(step.retry.on_exhausted)
    referenced.discard("done")

    first_step = next(iter(wf.steps))
    findings: list[RuleFinding] = []
    for step_name in wf.steps:
        if step_name != first_step and step_name not in referenced:
            findings.append(
                RuleFinding(
                    rule="unreachable-step",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' is not the entry point and no other step "
                        f"routes to it. It will never execute. Remove it or add routing."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="model-on-non-skill-step",
    description="The 'model' field only affects run_skill/run_skill_retry steps.",
    severity=Severity.WARNING,
)
def _check_model_on_non_skill(wf: Recipe) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.model and step.tool not in SKILL_TOOLS:
            findings.append(
                RuleFinding(
                    rule="model-on-non-skill-step",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' has 'model: {step.model}' but uses "
                        f"tool '{step.tool}'. The model field only affects "
                        f"run_skill and run_skill_retry. Remove it to avoid confusion."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="retry-without-capture",
    description="run_skill_retry with retry must have capture if downstream uses context.",
    severity=Severity.WARNING,
)
def _check_retry_without_capture(wf: Recipe) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    step_names = list(wf.steps.keys())

    for idx, (step_name, step) in enumerate(wf.steps.items()):
        if step.tool == "run_skill_retry" and step.retry and not step.capture:
            downstream_needs_context = False
            for later_name in step_names[idx + 1 :]:
                later_step = wf.steps[later_name]
                for val in later_step.with_args.values():
                    if "context." in str(val):
                        downstream_needs_context = True
                        break
                if downstream_needs_context:
                    break

            if downstream_needs_context:
                findings.append(
                    RuleFinding(
                        rule="retry-without-capture",
                        severity=Severity.WARNING,
                        step_name=step_name,
                        message=(
                            f"Step '{step_name}' uses run_skill_retry with retry "
                            f"routing but has no capture block. A downstream step "
                            f"references context values — add a capture block to "
                            f"thread outputs (e.g., worktree_path, plan_path) forward."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="worktree-retry-creates-new",
    description="Worktree-creating skills must not have retry max_attempts > 1.",
    severity=Severity.ERROR,
)
def _check_worktree_retry_creates_new(
    wf: Recipe,
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if not step.retry or step.retry.max_attempts <= 1:
            continue

        skill_cmd = step.with_args.get("skill_command", "")
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name and skill_name in _WORKTREE_CREATING_SKILLS:
            findings.append(
                RuleFinding(
                    rule="worktree-retry-creates-new",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' retries {skill_name} "
                        f"with max_attempts="
                        f"{step.retry.max_attempts}. Each retry "
                        f"creates a new worktree, orphaning partial "
                        f"progress. Set max_attempts: 1 and route "
                        f"on_exhausted to a retry-worktree step that "
                        f"resumes in the existing worktree."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="needs-retry-no-restart",
    description="Worktree-creating skills must not retry on needs_retry with max_attempts >= 1.",
    severity=Severity.ERROR,
)
def _check_needs_retry_no_restart(wf: Recipe) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if not step.retry:
            continue
        if step.retry.on != "needs_retry":
            continue
        if step.retry.max_attempts < 1:
            continue  # max_attempts: 0 is the correct pattern — escalates immediately
        skill_cmd = step.with_args.get("skill_command", "")
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name and skill_name in _WORKTREE_CREATING_SKILLS:
            findings.append(
                RuleFinding(
                    rule="needs-retry-no-restart",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' retries worktree-creating skill "
                        f"'{skill_name}' on needs_retry "
                        f"(max_attempts={step.retry.max_attempts}). "
                        f"needs_retry signals partial progress exists — the skill "
                        f"must not restart. "
                        f"Set max_attempts: 0 to immediately escalate to on_exhausted."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="retry-worktree-cwd",
    description="retry-worktree cwd must use a context variable so git runs inside the worktree.",
    severity=Severity.ERROR,
)
def _check_retry_worktree_cwd(wf: Recipe) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if resolve_skill_name(skill_cmd) != "retry-worktree":
            continue
        cwd = step.with_args.get("cwd", "")
        if "${{ context." not in cwd:
            findings.append(
                RuleFinding(
                    rule="retry-worktree-cwd",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=f"Step '{step_name}': retry-worktree cwd must use a context variable.",
                )
            )
    return findings


@semantic_rule(
    name="weak-constraint-text",
    description="Pipeline constraints must enumerate forbidden native tools by name.",
    severity=Severity.WARNING,
)
def _check_weak_constraint_text(wf: Recipe) -> list[RuleFinding]:
    if not wf.kitchen_rules:
        return []

    all_text = " ".join(wf.kitchen_rules)
    found = sum(1 for tool in PIPELINE_FORBIDDEN_TOOLS if tool in all_text)
    if found < len(PIPELINE_FORBIDDEN_TOOLS):
        tool_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)
        return [
            RuleFinding(
                rule="weak-constraint-text",
                severity=Severity.WARNING,
                step_name="(recipe)",
                message=(
                    "Recipe kitchen_rules do not enumerate forbidden native tools. "
                    f"Name specific tools ({tool_list}) "
                    "for orchestrator discipline."
                ),
            )
        ]
    return []


@semantic_rule(
    name="undeclared-capture-key",
    description=(
        "Capture references to result.X should match keys declared in the "
        "skill's outputs contract in skill_contracts.yaml."
    ),
    severity=Severity.WARNING,
)
def _check_capture_output_coverage(wf: Recipe) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()

    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        if not step.capture and not step.capture_list:
            continue

        skill_cmd = step.with_args.get("skill_command", "")
        skill_name = resolve_skill_name(skill_cmd)
        if not skill_name:
            # Dynamic or non-autoskillit skill_command — cannot validate.
            continue

        contract = get_skill_contract(skill_name, manifest)
        if contract is None:
            findings.append(
                RuleFinding(
                    rule="undeclared-capture-key",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' captures from skill '{skill_name}' "
                        f"which has no outputs contract entry in skill_contracts.yaml. "
                        f"Add an outputs section to verify capture correctness."
                    ),
                )
            )
            continue

        declared_keys = {out.name for out in contract.outputs}

        for _capture_var, capture_expr in step.capture.items():
            for ref_key in _RESULT_CAPTURE_RE.findall(capture_expr):
                if ref_key not in declared_keys:
                    findings.append(
                        RuleFinding(
                            rule="undeclared-capture-key",
                            severity=Severity.WARNING,
                            step_name=step_name,
                            message=(
                                f"Step '{step_name}' captures result.{ref_key} "
                                f"but skill '{skill_name}' does not declare '{ref_key}' "
                                f"in its outputs contract."
                            ),
                        )
                    )

        for _capture_var, capture_expr in step.capture_list.items():
            for ref_key in _RESULT_CAPTURE_RE.findall(capture_expr):
                if ref_key not in declared_keys:
                    findings.append(
                        RuleFinding(
                            rule="undeclared-capture-key",
                            severity=Severity.WARNING,
                            step_name=step_name,
                            message=(
                                f"Step '{step_name}' captures result.{ref_key} via capture_list "
                                f"but skill '{skill_name}' does not declare '{ref_key}' "
                                f"in its outputs contract."
                            ),
                        )
                    )

    return findings


@semantic_rule(
    name="dead-output",
    description="Captured variable never consumed downstream",
    severity=Severity.ERROR,
)
def _check_dead_output(wf: Recipe) -> list[RuleFinding]:
    """Error when any captured context variable is never consumed downstream."""
    from autoskillit.recipe.validator import analyze_dataflow

    report = analyze_dataflow(wf)
    return [
        RuleFinding(
            rule="dead-output",
            severity=Severity.ERROR,
            step_name=w.step_name,
            message=w.message,
        )
        for w in report.warnings
        if w.code == "DEAD_OUTPUT"
    ]


@semantic_rule(
    name="implicit-handoff",
    description="Skill with declared outputs missing capture block",
    severity=Severity.ERROR,
)
def _check_implicit_handoff(wf: Recipe) -> list[RuleFinding]:
    """Error when a skill step has contract outputs but no capture: block."""
    try:
        manifest = load_bundled_manifest()
    except Exception:
        logger.warning("implicit-handoff: failed to load skill_contracts.yaml; skipping")
        return []

    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not isinstance(skill_cmd, str):
            continue
        skill_name = resolve_skill_name(skill_cmd)
        if not skill_name:
            continue
        contract = manifest.get("skills", {}).get(skill_name)
        if not contract:
            continue
        if not contract.get("outputs"):
            continue
        if not step.capture:
            findings.append(
                RuleFinding(
                    rule="implicit-handoff",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' calls '/autoskillit:{skill_name}' "
                        f"which declares {len(contract['outputs'])} output(s) "
                        f"but has no capture: block. Add a capture: block to "
                        f"thread the skill's structured output into pipeline context."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="multipart-iteration-notes",
    description="Multi-part plan recipes must declare iteration conventions.",
    severity=Severity.ERROR,
)
def _check_multipart_iteration_notes(wf: Recipe) -> list[RuleFinding]:
    _MULTIPART_SKILLS = {"/autoskillit:make-plan", "/autoskillit:rectify"}
    findings: list[RuleFinding] = []

    has_multipart_step = any(
        step.tool in SKILL_TOOLS
        and any(s in step.with_args.get("skill_command", "") for s in _MULTIPART_SKILLS)
        for step in wf.steps.values()
    )
    if not has_multipart_step:
        return []

    plan_step = wf.steps.get("plan")
    plan_note = (plan_step.note or "") if plan_step is not None else ""
    # Also accept the glob pattern from the note of whichever step invokes the multipart skill
    multipart_step_notes = [
        (step.note or "")
        for step in wf.steps.values()
        if step.tool in SKILL_TOOLS
        and any(s in step.with_args.get("skill_command", "") for s in _MULTIPART_SKILLS)
    ]
    if "*_part_*.md" not in plan_note and not any(
        "*_part_*.md" in note for note in multipart_step_notes
    ):
        findings.append(
            RuleFinding(
                rule="multipart-glob-note",
                severity=Severity.ERROR,
                step_name="plan",
                message=(
                    "Recipe uses make-plan or rectify but neither the 'plan' step note nor "
                    "the planning step's own note contains '*_part_*.md'. Agents will not "
                    "know to glob for multi-part plan files. Add: "
                    "'Glob plan_dir for *_part_*.md or single plan file.' to the plan "
                    "step's note (or to the make-plan/rectify step's note if no separate "
                    "'plan' step exists)."
                ),
            )
        )

    sequential_keywords = ("SEQUENTIAL EXECUTION", "full cycle", "Never run verify for all parts")
    rules_text = " ".join(wf.kitchen_rules)
    if not any(kw in rules_text for kw in sequential_keywords):
        findings.append(
            RuleFinding(
                rule="multipart-sequential-kitchen-rule",
                severity=Severity.WARNING,
                step_name="kitchen_rules",
                message=(
                    "Recipe uses make-plan or rectify but kitchen_rules do not contain "
                    "a sequential execution constraint. Without it, agents may "
                    "batch-verify all parts before "
                    "implementing any. Add a rule such as: 'SEQUENTIAL EXECUTION: complete full "
                    "cycle per part before advancing.'"
                ),
            )
        )

    next_or_done = wf.steps.get("next_or_done")
    if next_or_done is not None and next_or_done.on_result is not None:
        if next_or_done.on_result.routes.get("more_parts") != "verify":
            findings.append(
                RuleFinding(
                    rule="multipart-route-back",
                    severity=Severity.ERROR,
                    step_name="next_or_done",
                    message=(
                        "Recipe uses make-plan or rectify but next_or_done does not route "
                        "'more_parts' back to 'verify'. Sequential part processing requires "
                        "more_parts → verify in the on_result routes."
                    ),
                )
            )

    return findings


@semantic_rule(
    name="merge-cleanup-uncaptured",
    description="merge_worktree steps should capture cleanup_succeeded to track orphaned results.",
    severity=Severity.WARNING,
)
def _check_merge_cleanup_captured(wf: Recipe) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    for step_name, step in wf.steps.items():
        if step.tool != "merge_worktree":
            continue
        # Check whether any capture value references cleanup_succeeded
        captures_cleanup = any("result.cleanup_succeeded" in str(v) for v in step.capture.values())
        if not captures_cleanup:
            findings.append(
                RuleFinding(
                    rule="merge-cleanup-uncaptured",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' calls merge_worktree but does not capture "
                        f"'cleanup_succeeded'. Add a capture entry such as "
                        f'cleanup_ok: "${{{{ result.cleanup_succeeded }}}}" '
                        f"so cleanup failures (orphaned worktree/branch) are not silently ignored."
                    ),
                )
            )

    return findings


@semantic_rule(
    name="clone-root-as-worktree",
    description=(
        "test_check/merge_worktree worktree_path must not trace back to "
        "result.clone_path — that is the clone root, not a git worktree."
    ),
    severity=Severity.ERROR,
)
def _check_clone_root_as_worktree(wf: Recipe) -> list[RuleFinding]:
    """Error when worktree_path for test_check/merge_worktree originates from clone_path.

    Builds a capture map by iterating recipe steps in declaration order.
    For each test_check or merge_worktree step, resolves the context variable
    used for worktree_path and checks whether it was captured from result.clone_path.
    """
    captures: dict[str, str] = {}  # var_name -> capture expression
    findings: list[RuleFinding] = []

    for step_name, step in wf.steps.items():
        if step.tool in ("test_check", "merge_worktree"):
            worktree_arg = step.with_args.get("worktree_path", "")
            if isinstance(worktree_arg, str):
                for var_name in _CONTEXT_REF_RE.findall(worktree_arg):
                    cap_expr = captures.get(var_name, "")
                    if "result.clone_path" in cap_expr:
                        findings.append(
                            RuleFinding(
                                rule="clone-root-as-worktree",
                                severity=Severity.ERROR,
                                step_name=step_name,
                                message=(
                                    f"Step '{step_name}' passes worktree_path via "
                                    f"'context.{var_name}', which was captured from "
                                    f"result.clone_path. clone_path is the root of the "
                                    f"cloned repository, not a git worktree. "
                                    f"Capture worktree_path from result.worktree_path "
                                    f"(e.g., from an implement-worktree step's capture block)."
                                ),
                            )
                        )

        # Update capture map AFTER the tool check so captures only affect later steps
        for cap_key, cap_val in step.capture.items():
            captures[cap_key] = str(cap_val)

    return findings


@semantic_rule(
    name="multipart-plan-parts-not-captured",
    description="Multi-part plan recipes must capture plan_parts via capture_list.",
    severity=Severity.ERROR,
)
def _check_plan_parts_captured(wf: Recipe) -> list[RuleFinding]:
    _MULTIPART_SKILLS = {"/autoskillit:make-plan", "/autoskillit:rectify"}
    findings: list[RuleFinding] = []

    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not any(s in skill_cmd for s in _MULTIPART_SKILLS):
            continue
        if "plan_parts" not in step.capture_list:
            findings.append(
                RuleFinding(
                    rule="multipart-plan-parts-not-captured",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' calls a multi-part skill but does not capture "
                        f"'plan_parts' via capture_list. Add: "
                        f'capture_list:\\n  plan_parts: "${{{{ result.plan_parts }}}}" '
                        f"so the full ordered list of part files is in pipeline context."
                    ),
                )
            )

    return findings


@semantic_rule(
    name="unbounded-cycle",
    description="Routing cycle with no structural termination guarantee",
    severity=Severity.ERROR,
)
def _check_unbounded_cycles(recipe: Recipe) -> list[RuleFinding]:
    """Detect routing cycles and classify by whether they have any exit guarantee.

    Severity rules:
    - ERROR: no step in the cycle has any exit edge — the cycle is a guaranteed infinite loop.
    - WARNING: at least one step has an exit edge (on_failure exits the SCC, or
      retry.max_attempts + on_exhausted exits the SCC) — the cycle can terminate
      conditionally, but relies on skill-internal discipline rather than schema enforcement.
    """
    from autoskillit.recipe.validator import _build_step_graph  # deferred — breaks circular import

    graph = _build_step_graph(recipe)
    findings: list[RuleFinding] = []
    reported_cycles: set[frozenset[str]] = set()

    visited: set[str] = set()
    rec_stack: set[str] = set()

    def dfs(node: str, path: list[str]) -> None:
        visited.add(node)
        rec_stack.add(node)
        for neighbor in graph.get(node, set()):
            if neighbor not in recipe.steps:
                continue  # dead reference — caught by validate_recipe
            if neighbor not in visited:
                dfs(neighbor, path + [neighbor])
            elif neighbor in rec_stack:
                # Reconstruct the cycle steps from the path
                if neighbor in path:
                    cycle_steps = path[path.index(neighbor) :]
                else:
                    cycle_steps = path
                cycle_key = frozenset(cycle_steps)
                if cycle_key in reported_cycles:
                    rec_stack.discard(node)
                    return
                reported_cycles.add(cycle_key)
                cycle_set = set(cycle_steps)

                # Structural exit: retry.max_attempts > 0 with on_exhausted outside cycle
                # (a retry block with max_attempts is a structural bound — no finding)
                has_retry_exit = any(
                    (r := recipe.steps[s].retry) is not None
                    and r.max_attempts > 0
                    and r.on_exhausted not in cycle_set
                    for s in cycle_steps
                    if s in recipe.steps
                )
                if has_retry_exit:
                    # Structurally bounded — no finding
                    rec_stack.discard(node)
                    return

                # Conditional exit: on_failure pointing outside the cycle (unbounded but escapable)
                has_failure_exit = any(
                    recipe.steps[s].on_failure is not None
                    and recipe.steps[s].on_failure not in cycle_set
                    for s in cycle_steps
                    if s in recipe.steps
                )

                if has_failure_exit:
                    severity = Severity.WARNING
                    message = (
                        f"Routing cycle detected: {' → '.join(cycle_steps)} → {neighbor}. "
                        f"The cycle has a conditional exit path but no structural bound on "
                        f"iterations. Add retry.max_attempts to at least one cycling step "
                        f"to enforce a maximum iteration count."
                    )
                else:
                    severity = Severity.ERROR
                    message = (
                        f"Routing cycle detected: {' → '.join(cycle_steps)} → {neighbor}. "
                        f"No step in this cycle has an exit edge — this cycle has no "
                        f"termination guarantee and will loop forever. Add retry.max_attempts "
                        f"with on_exhausted outside the cycle, or route on_failure to a step "
                        f"outside the cycle."
                    )
                findings.append(
                    RuleFinding(
                        rule="unbounded-cycle",
                        severity=severity,
                        step_name=node,
                        message=message,
                    )
                )
        rec_stack.discard(node)

    for step_name in recipe.steps:
        if step_name not in visited:
            dfs(step_name, [step_name])

    return findings


@semantic_rule(
    name="on-result-missing-failure-route",
    description=(
        "Tool and python steps using on_result must also declare on_failure. "
        "on_result only fires when the tool succeeds and returns a recognized verdict. "
        "When the tool call itself fails (success: false), on_result never evaluates "
        "and the orchestrator has no route without on_failure."
    ),
    severity=Severity.ERROR,
)
def _check_on_result_missing_failure_route(wf: Recipe) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        is_tool_invocation = step.tool is not None or step.python is not None
        if is_tool_invocation and step.on_result is not None and step.on_failure is None:
            findings.append(
                RuleFinding(
                    rule="on-result-missing-failure-route",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' uses on_result but has no on_failure. "
                        f"If the tool call fails before a verdict is returned, the "
                        f"orchestrator has no route. Add on_failure: <target>."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="stale-ref-after-merge",
    description=(
        "A context variable sourced from a worktree path or branch name is consumed "
        "by a step that executes after merge_worktree (or remove_clone), which deletes "
        "the resource the variable refers to."
    ),
    severity=Severity.WARNING,
)
def _check_stale_ref_after_merge(wf: Recipe) -> list[RuleFinding]:
    from autoskillit.recipe.validator import analyze_dataflow  # deferred — breaks circular import

    report = analyze_dataflow(wf)
    return [
        RuleFinding(
            rule="stale-ref-after-merge",
            severity=Severity.WARNING,
            step_name=w.step_name,
            message=w.message,
        )
        for w in report.warnings
        if w.code == "REF_INVALIDATED"
    ]


@semantic_rule(
    name="push-before-audit",
    description=(
        "A push_to_remote step is reachable from the entry point without "
        "passing through an audit-impl skill step first"
    ),
    severity=Severity.WARNING,
)
def _check_push_before_audit(wf: Recipe) -> list[RuleFinding]:
    """Detect recipes where push_to_remote can fire before audit-impl evaluates quality.

    Algorithm:
    1. Locate all steps whose tool is push_to_remote (push_steps).
       If none exist, return early — the rule is silent.
    2. Locate all run_skill / run_skill_retry steps whose skill_command contains
       "audit-impl" (audit_steps).
    3. Build the full routing graph via _build_step_graph.
    4. BFS from the entry point.  When the frontier visits an audit_step, it is
       added to reachable_without_audit but NOT expanded — treating audit_steps as
       one-way barriers.  All steps reachable before crossing audit land in
       reachable_without_audit.
    5. Any push_step found in reachable_without_audit is a violation: there exists
       at least one execution path that reaches the push without passing through audit.
    """
    from autoskillit.recipe.validator import _build_step_graph  # deferred — breaks circular import

    push_steps = {name for name, step in wf.steps.items() if step.tool == "push_to_remote"}
    if not push_steps:
        return []

    audit_steps = {
        name
        for name, step in wf.steps.items()
        if step.tool in SKILL_TOOLS and "audit-impl" in step.with_args.get("skill_command", "")
    }

    graph = _build_step_graph(wf)
    entry = next(iter(wf.steps))

    reachable_without_audit: set[str] = set()
    queue = [entry]
    while queue:
        node = queue.pop()
        if node in reachable_without_audit:
            continue
        reachable_without_audit.add(node)
        if node in audit_steps:
            continue  # barrier: do not expand beyond the first audit step on this path
        for successor in graph.get(node, set()):
            if successor not in reachable_without_audit:
                queue.append(successor)

    violations = sorted(push_steps & reachable_without_audit)
    return [
        RuleFinding(
            rule="push-before-audit",
            severity=Severity.WARNING,
            step_name=name,
            message=(
                f"'{name}' uses push_to_remote but is reachable from the entry "
                "point without passing through an audit-impl skill step. "
                "Ensure audit-impl runs before any push_to_remote."
            ),
        )
        for name in violations
    ]
