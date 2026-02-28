"""Recipe validation — structural, semantic rules, and dataflow analysis."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from autoskillit.core import (
    PIPELINE_FORBIDDEN_TOOLS,
    RETRY_RESPONSE_FIELDS,
    SKILL_TOOLS,
    Severity,
    get_logger,
)
from autoskillit.recipe.contracts import (
    _CONTEXT_REF_RE,
    _INPUT_REF_RE,
    _RESULT_CAPTURE_RE,
    _TEMPLATE_REF_RE,
    count_positional_args,
    extract_context_refs,
    extract_input_refs,
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.recipe.io import iter_steps_with_context
from autoskillit.recipe.schema import DataFlowReport, DataFlowWarning, Recipe

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Severity findings and rule registry
# ---------------------------------------------------------------------------


@dataclass
class RuleFinding:
    """A single finding produced by a semantic rule."""

    rule: str
    severity: Severity
    step_name: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "rule": self.rule,
            "severity": self.severity.value,
            "step": self.step_name,
            "message": self.message,
        }


@dataclass
class RuleSpec:
    """Internal: metadata for one registered rule."""

    name: str
    description: str
    severity: Severity
    check: Callable[[Recipe], list[RuleFinding]]


_RULE_REGISTRY: list[RuleSpec] = []


def semantic_rule(
    name: str,
    description: str,
    severity: Severity = Severity.WARNING,
) -> Callable:
    """Decorator that registers a semantic validation rule."""

    def decorator(
        fn: Callable[[Recipe], list[RuleFinding]],
    ) -> Callable[[Recipe], list[RuleFinding]]:
        _RULE_REGISTRY.append(
            RuleSpec(name=name, description=description, severity=severity, check=fn)
        )
        return fn

    return decorator


def run_semantic_rules(wf: Recipe) -> list[RuleFinding]:
    """Execute all registered semantic rules against a workflow."""
    findings: list[RuleFinding] = []
    for spec in _RULE_REGISTRY:
        findings.extend(spec.check(wf))
    return findings


def findings_to_dicts(findings: list[RuleFinding]) -> list[dict[str, str]]:
    """Convert a list of RuleFindings to serializable dicts."""
    return [f.to_dict() for f in findings]


def filter_version_rule(suggestions: list[dict[str, str]]) -> list[dict[str, str]]:
    """Remove 'outdated-recipe-version' rule findings from suggestions."""
    return [s for s in suggestions if s.get("rule") != "outdated-recipe-version"]


def build_quality_dict(report: DataFlowReport) -> dict[str, object]:
    """Build the quality analysis dict from a DataFlowReport."""
    return {
        "warnings": [
            {
                "code": w.code,
                "step": w.step_name,
                "field": w.field,
                "message": w.message,
            }
            for w in report.warnings
        ],
        "summary": report.summary,
    }


def compute_recipe_validity(
    errors: list[str],
    semantic_findings: list[RuleFinding],
    contract_findings: list[dict],  # type: ignore[type-arg]
) -> bool:
    """Return True if no schema, semantic, or contract errors are present."""
    has_schema_errors = bool(errors)
    has_semantic_errors = any(f.severity == Severity.ERROR for f in semantic_findings)
    has_contract_errors = any(f.get("severity") == "error" for f in contract_findings)
    return not has_schema_errors and not has_semantic_errors and not has_contract_errors


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


def validate_recipe(recipe: Recipe) -> list[str]:
    """Return a list of validation errors (empty if valid)."""
    errors: list[str] = []

    if not recipe.name:
        errors.append("Recipe must have a 'name'.")
    if not recipe.steps:
        errors.append("Recipe must have at least one step.")

    step_names = set(recipe.steps.keys())

    for step_name, step in recipe.steps.items():
        discriminators = [d for d in ("tool", "action", "python") if getattr(step, d) is not None]
        if len(discriminators) == 0:
            errors.append(f"Step '{step_name}' must have 'tool', 'action', or 'python'.")
        if len(discriminators) > 1:
            errors.append(
                f"Step '{step_name}' has multiple discriminators "
                f"({', '.join(discriminators)}); pick one."
            )
        if step.python is not None and "." not in step.python:
            errors.append(
                f"Step '{step_name}'.python must be a dotted path "
                f"(module.function), got '{step.python}'."
            )
        if step.action == "stop" and not step.message:
            errors.append(f"Terminal step '{step_name}' (action: stop) must have a 'message'.")
        for goto_field in ("on_success", "on_failure"):
            target = getattr(step, goto_field)
            if target and target not in step_names and target != "done":
                errors.append(
                    f"Step '{step_name}'.{goto_field} references unknown step '{target}'."
                )
        if step.retry and step.retry.on_exhausted not in step_names:
            errors.append(
                f"Step '{step_name}'.retry.on_exhausted references "
                f"unknown step '{step.retry.on_exhausted}'."
            )
        if step.retry and step.retry.on and step.retry.on not in RETRY_RESPONSE_FIELDS:
            errors.append(
                f"Step '{step_name}'.retry.on references unknown response field "
                f"'{step.retry.on}'. Valid fields: {sorted(RETRY_RESPONSE_FIELDS)}"
            )
        if step.on_result is not None:
            if step.on_success is not None:
                errors.append(
                    f"Step '{step_name}' has both 'on_result' and 'on_success'; "
                    f"they are mutually exclusive."
                )
            if not step.on_result.field:
                errors.append(f"Step '{step_name}'.on_result.field must be non-empty.")
            if not step.on_result.routes:
                errors.append(f"Step '{step_name}'.on_result.routes must be non-empty.")
            for value, target in step.on_result.routes.items():
                if target not in step_names and target != "done":
                    errors.append(
                        f"Step '{step_name}'.on_result.routes.{value} references "
                        f"unknown step '{target}'."
                    )

    # Validate capture values: must contain ${{ result.* }} expressions
    for step_name, step in recipe.steps.items():
        for cap_key, cap_val in step.capture.items():
            all_refs = _TEMPLATE_REF_RE.findall(cap_val)
            if not all_refs:
                errors.append(
                    f"Step '{step_name}'.capture.{cap_key} must contain "
                    f"a ${{{{ result.* }}}} expression."
                )
            for ref_match in all_refs:
                inner = ref_match[3:-2].strip()
                if not inner.startswith("result."):
                    errors.append(
                        f"Step '{step_name}'.capture.{cap_key} references "
                        f"'{inner}'; capture values must use the 'result.' namespace."
                    )

    # Validate input and context references in with_args using iter_steps_with_context
    ingredient_names = set(recipe.ingredients.keys())

    for step_name, step, available_context in iter_steps_with_context(recipe):
        for arg_key, arg_val in step.with_args.items():
            if not isinstance(arg_val, str):
                continue
            for ref in _INPUT_REF_RE.findall(arg_val):
                if ref not in ingredient_names:
                    errors.append(
                        f"Step '{step_name}'.with.{arg_key} references undeclared input '{ref}'."
                    )
            for ref in _CONTEXT_REF_RE.findall(arg_val):
                if ref not in available_context:
                    errors.append(
                        f"Step '{step_name}'.with.{arg_key} references "
                        f"context variable '{ref}' which has not been "
                        f"captured by a preceding step."
                    )

    if not recipe.kitchen_rules:
        errors.append(
            "Recipe has no 'kitchen_rules' field. Recipes should include "
            "orchestrator discipline constraints."
        )

    return errors


# ---------------------------------------------------------------------------
# Data-flow analysis
# ---------------------------------------------------------------------------


def _build_step_graph(recipe: Recipe) -> dict[str, set[str]]:
    """Build a routing adjacency list from all step routing fields.

    Each key is a step name, each value is the set of step names
    reachable in one hop (successors). Terminal targets like "done"
    are excluded since they are not real steps.
    """
    step_names = set(recipe.steps.keys())
    graph: dict[str, set[str]] = {name: set() for name in step_names}

    for name, step in recipe.steps.items():
        for target in (step.on_success, step.on_failure):
            if target and target in step_names:
                graph[name].add(target)
        if step.on_result:
            for target in step.on_result.routes.values():
                if target in step_names:
                    graph[name].add(target)
        if step.retry and step.retry.on_exhausted in step_names:
            graph[name].add(step.retry.on_exhausted)

    return graph


def _detect_dead_outputs(recipe: Recipe, graph: dict[str, set[str]]) -> list[DataFlowWarning]:
    """Detect captured variables that are never consumed downstream."""
    warnings: list[DataFlowWarning] = []

    for step_name, step in recipe.steps.items():
        if not step.capture:
            continue

        # BFS: collect all steps reachable from this step's successors
        reachable: set[str] = set()
        frontier = list(graph.get(step_name, set()))
        while frontier:
            current = frontier.pop()
            if current in reachable:
                continue
            reachable.add(current)
            frontier.extend(graph.get(current, set()))

        # Collect all context.X references in reachable steps' with_args
        consumed: set[str] = set()
        for reachable_name in reachable:
            reachable_step = recipe.steps[reachable_name]
            for arg_val in reachable_step.with_args.values():
                if not isinstance(arg_val, str):
                    continue
                consumed.update(_CONTEXT_REF_RE.findall(arg_val))

        # on_result routing on a captured key is structural consumption
        if step.on_result and step.on_result.field in step.capture:
            consumed.add(step.on_result.field)

        # Flag captured vars not consumed on any path
        for cap_key in step.capture:
            if cap_key not in consumed:
                # Exempt merge_worktree diagnostic captures: cleanup_succeeded is captured
                # for observability (to surface orphaned worktrees), not for data-passing.
                # The merge-cleanup-uncaptured rule requires this capture; exempting it
                # from dead-output prevents the two rules from conflicting.
                cap_val = step.capture.get(cap_key, "")
                if step.tool == "merge_worktree" and "result.cleanup_succeeded" in str(cap_val):
                    continue
                warnings.append(
                    DataFlowWarning(
                        code="DEAD_OUTPUT",
                        step_name=step_name,
                        field=cap_key,
                        message=(
                            f"Step '{step_name}' captures '{cap_key}' but no "
                            f"reachable downstream step references "
                            f"${{{{ context.{cap_key} }}}}."
                        ),
                    )
                )

    return warnings


def _detect_implicit_handoffs(recipe: Recipe) -> list[DataFlowWarning]:
    """Detect skill-invoking steps with no capture block."""
    warnings: list[DataFlowWarning] = []

    for step_name, step in recipe.steps.items():
        if step.tool in SKILL_TOOLS and not step.capture:
            warnings.append(
                DataFlowWarning(
                    code="IMPLICIT_HANDOFF",
                    step_name=step_name,
                    field=step.tool,
                    message=(
                        f"Step '{step_name}' calls '{step.tool}' but has no "
                        f"capture: block. Data flows to subsequent steps "
                        f"implicitly through agent context rather than "
                        f"explicit ${{{{ context.X }}}} wiring."
                    ),
                )
            )

    return warnings


def analyze_dataflow(recipe: Recipe) -> DataFlowReport:
    """Analyze pipeline data flow quality (non-blocking warnings).

    Unlike validate_recipe() which returns blocking errors for
    structural problems, this function returns advisory warnings
    about data-flow quality: dead outputs, implicit hand-offs,
    and a summary.
    """
    graph = _build_step_graph(recipe)

    warnings: list[DataFlowWarning] = []
    warnings.extend(_detect_dead_outputs(recipe, graph))
    warnings.extend(_detect_implicit_handoffs(recipe))

    if warnings:
        summary = f"{len(warnings)} data-flow warning{'s' if len(warnings) != 1 else ''} found."
    else:
        summary = (
            "No data-flow warnings. All captures are consumed"
            " and skill outputs are explicitly wired."
        )

    return DataFlowReport(warnings=warnings, summary=summary)


# ---------------------------------------------------------------------------
# Semantic rules
# ---------------------------------------------------------------------------

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

    from autoskillit import __version__

    script_ver = wf.version
    if script_ver is None:
        return [
            RuleFinding(
                rule="outdated-recipe-version",
                severity=Severity.WARNING,
                step_name="(top-level)",
                message=(
                    f"Recipe has no autoskillit_version field. "
                    f"Current installed version is {__version__}. "
                    f"Run 'autoskillit migrate' to update."
                ),
            )
        ]

    if Version(script_ver) < Version(__version__):
        return [
            RuleFinding(
                rule="outdated-recipe-version",
                severity=Severity.WARNING,
                step_name="(top-level)",
                message=(
                    f"Recipe version {script_ver} is behind installed "
                    f"version {__version__}. Run 'autoskillit migrate' to update."
                ),
            )
        ]

    return []


@semantic_rule(
    name="missing-ingredient",
    description=(
        "Skill steps must provide all required ingredients via context or recipe "
        "ingredient references. Detects when a skill requires an ingredient that the "
        "step does not reference."
    ),
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
        if step.on_success:
            referenced.add(step.on_success)
        if step.on_failure:
            referenced.add(step.on_failure)
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
    description=(
        "run_skill_retry steps with retry routing that feed downstream "
        "context references must have capture blocks to supply those values."
    ),
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
    description=(
        "Worktree-creating skills (implement-worktree, "
        "implement-worktree-no-merge) must not have retry "
        "max_attempts > 1. Each retry re-invokes the skill, "
        "creating a new worktree and orphaning the previous one. "
        "Use max_attempts: 1 and route on_exhausted to a "
        "retry-worktree step instead."
    ),
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
    description=(
        "Worktree-creating skills (implement-worktree, implement-worktree-no-merge) "
        "must not retry on needs_retry with max_attempts >= 1. "
        "needs_retry signals partial progress exists in an existing worktree — "
        "retrying the skill unconditionally creates a new timestamped worktree, "
        "discarding that partial work. "
        "Set max_attempts: 0 so on_exhausted fires immediately and routes to "
        "a retry-worktree step that resumes in the existing worktree."
    ),
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
    name="weak-constraint-text",
    description=(
        "Pipeline constraints should enumerate forbidden native tools by name. "
        "Generic one-liner constraints like 'Only use MCP tools' are too vague "
        "to enforce orchestrator discipline."
    ),
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
    description=(
        "Recipes with make-plan or rectify steps must declare multi-part iteration conventions: "
        "the plan step note must contain the *_part_*.md glob pattern, kitchen_rules must include "
        "a sequential execution constraint, and next_or_done must route more_parts back to verify."
    ),
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
    description=(
        "merge_worktree steps should capture cleanup_succeeded to surface orphaned "
        "worktrees or branches left behind when cleanup commands fail after a successful merge."
    ),
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
    name="multipart-plan-parts-not-captured",
    description=(
        "Recipes with make-plan or rectify steps must capture plan_parts via capture_list "
        "so the full ordered list of part files is available in pipeline context."
    ),
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
