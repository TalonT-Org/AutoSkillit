"""Input and ingredient validation rules for recipe pipelines."""

from __future__ import annotations

import regex as re
from packaging.version import Version

from autoskillit.core import (
    AUTOSKILLIT_INSTALLED_VERSION,
    SKILL_TOOLS,
    Severity,
    get_logger,
)
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import (
    classify_step_arg_style,
    count_positional_args,
    extract_context_refs,
    extract_input_refs,
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
        return []

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
                    all_input_names = {i.name for i in contract.inputs}
                    if classify_step_arg_style(skill_cmd, all_input_names) != "named":
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


@semantic_rule(
    name="ingredient-type-default-invalid",
    description=(
        "Integer-typed ingredients must not use '' as default "
        "(auto-detect sentinel is invalid for numerics)"
    ),
    severity=Severity.ERROR,
)
def _check_ingredient_type_default_invalid(ctx: ValidationContext) -> list[RuleFinding]:
    """Reject integer-typed ingredients that use '' as default or have non-parseable defaults."""
    wf = ctx.recipe
    findings: list[RuleFinding] = []

    for ing_name, ing in (wf.ingredients or {}).items():
        if getattr(ing, "type", None) != "integer":
            continue

        default = getattr(ing, "default", None)
        required = getattr(ing, "required", False)

        # Empty string default is the auto-detect sentinel — invalid for integer types
        if default == "":
            findings.append(
                RuleFinding(
                    rule="ingredient-type-default-invalid",
                    severity=Severity.ERROR,
                    step_name=ing_name,
                    message=(
                        f"Ingredient '{ing_name}' has type='integer' but default='' "
                        "(auto-detect sentinel). Integer-typed ingredients must use an "
                        "explicit numeric default. Suggested fix: default='3'."
                    ),
                )
            )
            continue

        # Non-empty but non-parseable default
        if default is not None and default != "":
            try:
                int(default)
                continue
            except ValueError as exc:
                findings.append(
                    RuleFinding(
                        rule="ingredient-type-default-invalid",
                        severity=Severity.ERROR,
                        step_name=ing_name,
                        message=(
                            f"Ingredient '{ing_name}' has type='integer' but default={default!r} "
                            f"is not parseable as an integer ({exc})."
                        ),
                    )
                )
            continue

        # None default with required=False and no explicit value — silently absent numeric field
        if default is None and not required:
            findings.append(
                RuleFinding(
                    rule="ingredient-type-default-invalid",
                    severity=Severity.ERROR,
                    step_name=ing_name,
                    message=(
                        f"Ingredient '{ing_name}' has type='integer', required=False, and "
                        f"default=None. Integer-typed non-required ingredients must declare "
                        f"an explicit default to avoid ambiguity."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="local-rounds-max-retries-alignment",
    description=(
        "local_review_rounds default must be less than review_max_retries default "
        "for the local-to-github mode graduation to occur with default configuration."
    ),
    severity=Severity.WARNING,
)
def _check_local_rounds_alignment(ctx: ValidationContext) -> list[RuleFinding]:
    ingredients = ctx.recipe.ingredients
    local_rounds_ing = ingredients.get("local_review_rounds")
    max_retries_ing = ingredients.get("review_max_retries")
    if not local_rounds_ing or not max_retries_ing:
        return []
    if local_rounds_ing.default is None:
        return []
    try:
        local_default = int(local_rounds_ing.default)
        max_default = int(max_retries_ing.default if max_retries_ing.default is not None else "3")
    except (ValueError, TypeError):
        return []
    if local_default == 0 or local_default < max_default:
        return []
    return [
        RuleFinding(
            rule="local-rounds-max-retries-alignment",
            severity=Severity.WARNING,
            step_name="(ingredients)",
            message=(
                f"local_review_rounds default ({local_default}) >= "
                f"review_max_retries default ({max_default}). Mode will never "
                f"transition from local to github with default configuration."
            ),
        )
    ]


_DISPLAY_ONLY_VALUES: frozenset[str] = frozenset({"on", "off", "auto-detect"})

_INPUTS_CONDITION_RE = re.compile(r"\$\{\{\s*inputs\.(\w+)\s*\}\}\s*(?:==|!=)\s*'([^']*)'")


@semantic_rule(
    name="ingredient-condition-value-domain",
    description=(
        "A when: condition compares inputs.<name> against a value that is not "
        "in the ingredient's valid value domain, or uses a display-only value "
        "('on', 'off', 'auto-detect') that diverges from the raw YAML default."
    ),
    severity=Severity.ERROR,
)
def _check_ingredient_condition_value_domain(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    ingredients = ctx.recipe.ingredients
    for step_name, step in ctx.recipe.steps.items():
        if not step.on_result:
            continue
        for cond in step.on_result.conditions:
            if cond.when is None:
                continue
            for match in _INPUTS_CONDITION_RE.finditer(cond.when):
                ing_name, operand = match.group(1), match.group(2)
                ing = ingredients.get(ing_name)
                if ing is None:
                    continue
                if operand in _DISPLAY_ONLY_VALUES:
                    findings.append(
                        RuleFinding(
                            rule="ingredient-condition-value-domain",
                            severity=Severity.ERROR,
                            step_name=step_name,
                            message=(
                                f"Condition on inputs.{ing_name} uses display-only "
                                f"value '{operand}' — use the raw YAML value instead "
                                f"(ingredient default: '{ing.default}')."
                            ),
                        )
                    )
                elif ing.default in ("true", "false") and operand.lower() not in ("true", "false"):
                    findings.append(
                        RuleFinding(
                            rule="ingredient-condition-value-domain",
                            severity=Severity.ERROR,
                            step_name=step_name,
                            message=(
                                f"Condition on inputs.{ing_name} uses '{operand}' "
                                f"which is not in the boolean value domain "
                                f"{{'true', 'false'}} (ingredient default: '{ing.default}')."
                            ),
                        )
                    )
    return findings


_KNOWN_CONFIG_AUTHORITY_KEYS: frozenset[str] = frozenset(
    {
        "source_dir",
        "base_branch",
        "local_review_rounds",
        "adversarial_review_level",
        "post_run_diagnostics",
        "is_fleet_dispatch",
        "dispatch_id",
    }
)


@semantic_rule(
    name="config-authority-requires-resolve-source",
    description=(
        "Ingredients with authority='config' must use a key resolvable"
        " by resolve_ingredient_defaults"
    ),
    severity=Severity.ERROR,
)
def _check_config_authority_requires_resolve_source(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, ing in ctx.recipe.ingredients.items():
        if getattr(ing, "authority", None) != "config":
            continue
        if name not in _KNOWN_CONFIG_AUTHORITY_KEYS:
            findings.append(
                RuleFinding(
                    rule="config-authority-requires-resolve-source",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=(
                        f"Ingredient {name!r} declares authority='config' but is not a key "
                        f"returned by resolve_ingredient_defaults(). "
                        f"Known config-authoritative keys: "
                        f"{sorted(_KNOWN_CONFIG_AUTHORITY_KEYS)}. "
                        "Remove the authority field or use a supported key."
                    ),
                )
            )
        elif getattr(ing, "required", False) and getattr(ing, "default", None) is None:
            findings.append(
                RuleFinding(
                    rule="config-authority-requires-resolve-source",
                    severity=Severity.WARNING,
                    step_name="(top-level)",
                    message=(
                        f"Ingredient {name!r} declares authority='config' and required=True "
                        "with no default — config always supplies the value, so required=True "
                        "is redundant."
                    ),
                )
            )
    return findings
