"""Input and ingredient validation rules for recipe pipelines."""

from __future__ import annotations

import re

from packaging.version import Version

from autoskillit.core import (
    AUTOSKILLIT_INSTALLED_VERSION,
    SKILL_TOOLS,
    Severity,
    get_logger,
)
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import (
    count_positional_args,
    extract_context_refs,
    extract_input_refs,
    extract_skill_cmd_refs,
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.recipe.io import iter_steps_with_context
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import _TERMINAL_TARGETS

logger = get_logger(__name__)


@semantic_rule(
    name="outdated-recipe-version",
    description="Recipe's autoskillit_version is below the installed package version",
    severity=Severity.WARNING,
)
def _check_outdated_version(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
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
def _check_unsatisfied_skill_input(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
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

                    # If the skill_command has template refs whose variable names
                    # don't all match named contract inputs, the step is using
                    # positional-style invocation (e.g., ${{ context.work_dir }}
                    # mapped positionally to a `worktree_path` input). We cannot
                    # validate positional mappings by name, so skip the check.
                    all_input_names = {i.name for i in contract.inputs}
                    cmd_refs = extract_skill_cmd_refs(skill_cmd)
                    if cmd_refs and not cmd_refs.issubset(all_input_names):
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
    name="missing-recommended-input",
    description="Skill steps should provide recommended inputs for full-quality output.",
    severity=Severity.WARNING,
)
def _check_missing_recommended_input(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()

    for step_name, step, _available_context in iter_steps_with_context(wf):
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command", "") if step.with_args else ""
        if not skill_cmd:
            continue
        skill_name = resolve_skill_name(skill_cmd)
        if not skill_name:
            continue
        contract = get_skill_contract(skill_name, manifest)
        if not contract:
            continue

        for inp in contract.inputs:
            if not inp.recommended or inp.required:
                continue
            if not re.search(rf"(?:^|\s){re.escape(inp.name)}=", skill_cmd):
                findings.append(
                    RuleFinding(
                        rule="missing-recommended-input",
                        severity=Severity.WARNING,
                        step_name=step_name,
                        message=(
                            f"Step '{step_name}' invokes {skill_name} which recommends "
                            f"'{inp.name}' for full-quality output, but the step does not "
                            f"pass it. Add '{inp.name}=${{{{ context.{inp.name} }}}}' to "
                            f"the skill_command or add a pre-computation step."
                        ),
                    )
                )

    return findings


@semantic_rule(
    name="shadowed-required-input",
    description=(
        "A skill step uses inline positional text for an argument that the skill's contract "
        "declares as required, and that argument is already available in the recipe context. "
        "Replace the prose placeholder with ${{ context.<name> }} or ${{ inputs.<name> }}."
    ),
    severity=Severity.ERROR,
)
def _check_shadowed_required_inputs(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()
    ingredient_names = set(wf.ingredients.keys())

    for step_name, step, available_context in iter_steps_with_context(wf):
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command", "") if step.with_args else ""
        if not skill_cmd:
            continue
        # Only applies when there are positional (non-template) args.
        # Steps with count == 0 are already handled by missing-ingredient.
        if count_positional_args(skill_cmd) == 0:
            continue
        skill_name = resolve_skill_name(skill_cmd)
        if not skill_name:
            continue
        contract = get_skill_contract(skill_name, manifest)
        if not contract:
            continue

        used_refs = extract_context_refs(step) | extract_input_refs(step)

        for req_input in contract.inputs:
            if not req_input.required:
                continue
            name = req_input.name
            if name in used_refs:
                continue  # Correctly passed as template ref
            # Only fire when the input IS available — if it's not in context yet,
            # the missing-ingredient rule (or runtime) will surface that separately.
            if name not in available_context and name not in ingredient_names:
                continue
            findings.append(
                RuleFinding(
                    rule="shadowed-required-input",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' invokes /{skill_name} which requires "
                        f"'{name}' (type: {req_input.type}), and '{name}' is available "
                        f"in the recipe context, but the skill_command passes prose text "
                        f"instead of the template reference. "
                        f"Replace the prose placeholder with "
                        f"'${{{{ context.{name} }}}}'."
                    ),
                )
            )

    return findings


@semantic_rule(
    name="unreachable-step",
    description="Steps that no other step routes to (and are not the entry point) are dead code.",
    severity=Severity.WARNING,
)
def _check_unreachable_steps(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    if not wf.steps:
        return []

    referenced: set[str] = set()
    for step in wf.steps.values():
        for field in ("on_success", "on_failure", "on_context_limit"):
            target = getattr(step, field, None)
            if target:
                referenced.add(target)
        if step.on_result:
            referenced.update(step.on_result.routes.values())
            for cond in step.on_result.conditions:
                referenced.add(cond.route)
        if step.on_exhausted:
            referenced.add(step.on_exhausted)
    for sentinel in _TERMINAL_TARGETS:
        referenced.discard(sentinel)

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


_PIPELINE_INTERNAL_PATTERN = re.compile(
    r"(?i)^(Set to |Set by |Set when |Used by |Passed by )|"
    r"\b(upstream orchestrat|already claimed|batch orchestrat)\b"
)


@semantic_rule(
    name="pipeline-internal-not-hidden",
    severity=Severity.WARNING,
    description=(
        "Ingredient description suggests pipeline-internal use (set by automation, "
        "not by users) but hidden: true is not set. Add hidden: true to suppress "
        "this ingredient from the agent's ingredients table."
    ),
)
def _check_pipeline_internal_not_hidden(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, ing in (ctx.recipe.ingredients or {}).items():
        if getattr(ing, "hidden", False):
            continue
        desc = getattr(ing, "description", "") or ""
        if _PIPELINE_INTERNAL_PATTERN.search(desc):
            findings.append(
                RuleFinding(
                    rule="pipeline-internal-not-hidden",
                    severity=Severity.WARNING,
                    step_name=name,
                    message=(
                        f"Ingredient '{name}' description suggests it is set by pipeline "
                        f"automation, not by users. Add `hidden: true` to suppress it from "
                        f"the agent's ingredients table."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="required-ingredient-no-default",
    severity=Severity.WARNING,
    description=(
        "Ingredient with required=True and no default may cause the orchestrator "
        "to call AskUserQuestion before open_kitchen."
    ),
)
def _check_required_without_default(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, ing in (ctx.recipe.ingredients or {}).items():
        if getattr(ing, "hidden", False):
            continue
        if getattr(ing, "required", False) and getattr(ing, "default", None) is None:
            findings.append(
                RuleFinding(
                    rule="required-ingredient-no-default",
                    severity=Severity.WARNING,
                    step_name=f"ingredient:{name}",
                    message=(
                        f"Ingredient '{name}' is required but has no default value. "
                        "This may cause the orchestrator to call AskUserQuestion "
                        "before open_kitchen. Consider adding a default value or "
                        "marking as hidden."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="research-output-mode-enum",
    severity=Severity.ERROR,
    description=(
        "The research recipe's output_mode ingredient default must be 'pr' or 'local'. "
        "Any other value is rejected at validation time."
    ),
)
def _check_research_output_mode_enum(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    wf = ctx.recipe
    if wf.name != "research":
        return []
    ing = (wf.ingredients or {}).get("output_mode")
    if ing is None:
        return []
    default = getattr(ing, "default", None)
    if default not in {"pr", "local"}:
        return [
            RuleFinding(
                rule="research-output-mode-enum",
                severity=Severity.ERROR,
                step_name="output_mode",
                message=(
                    f"output_mode.default must be 'pr' or 'local', got {default!r}. "
                    "Only two modes are supported (issue body overrides gist §1)."
                ),
            )
        ]
    return []
