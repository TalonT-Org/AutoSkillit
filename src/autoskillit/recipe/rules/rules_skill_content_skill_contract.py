"""SKILL.md skill-contract semantic rules.

Family of @semantic_rule checks that validate contract fields declared by the
skill manifest (pseudocode allowlist, output-section directives, executable-field
content validity, source-attribution directives, inline-content prohibitions).

`_resolve_skill_md` and `load_bundled_manifest` are NOT imported at module
scope. Every rule body that calls them performs a function-body-scoped lazy
import against `autoskillit.recipe.rules.rules_skill_content` (the facade) so
that `patch.object(rules_skill_content, "_resolve_skill_md", ...)` and
`patch("autoskillit.recipe.rules.rules_skill_content.load_bundled_manifest", ...)`
continue to redirect rule-body lookups via the facade namespace.
"""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_placeholder_parser import (
    _VRULE_RE,
    extract_bash_blocks,
    extract_bash_placeholders,
    extract_blockquote_placeholders,
    extract_blockquote_sections,
    extract_declared_ingredients,
    shell_vars_assigned,
)
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

_PSEUDOCODE_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("implement-worktree", "test_command"),
        ("implement-worktree-no-merge", "test_command"),
        ("resolve-failures", "test_command"),
        # ── research experiment skills: slug is the experiment directory name ─────────
        # Derived at runtime from the `name:` field of the experiment's environment.yml.
        # The prose in both skills explicitly describes how to derive it before the
        # bash blocks that reference it.
        ("implement-experiment", "slug"),
        ("generate-report", "slug"),
        ("setup-environment", "slug"),
        ("promote-to-main", "branch"),
        ("promote-to-main", "merge_base_sha"),
        ("promote-to-main", "number"),
        ("promote-to-main", "pr_title"),
        ("promote-to-main", "pr_url"),
        ("promote-to-main", "timestamp"),
        ("audit-impl", "implementation_ref"),
        ("dry-walkthrough", "issue_number"),
    }
)

_NO_MARKDOWN_DIRECTIVE_PATTERN: re.Pattern[str] = re.compile(
    r"no\s+markdown\s+format|plain\s+text.*token|literal\s+plain\s+text",
    re.IGNORECASE,
)

_EXECUTABLE_FIELD_SKILLS: frozenset[str] = frozenset(
    {
        "plan-experiment",
    }
)

_CONTENT_VALIDITY_SIGNALS_RE = re.compile(
    r"unresolved.{0,20}placeholder|template.{0,20}syntax|placeholder.{0,20}reject"
    r"|must not contain.{0,20}placeholder|reject.{0,20}template|\{[a-z_][a-z0-9_]*\}"
    r"|reject.{0,10}invalid",
    re.IGNORECASE,
)

_SOURCE_PROHIBITION_RE = re.compile(
    r"(?:NOT|NEVER|DO NOT)[\s\S]{0,120}?"
    r"(?:issue\s+title|issue\s+body|issue\s+metadata|closing_issue|"
    r"branch\s+names|ambient\s+context|re-?deriv|overrid|substitut)"
    r"[\s\S]{0,120}?"
    r"(?:task_title|title|## Title)",
    re.IGNORECASE,
)

_BANNED_BLOCKQUOTE_VARS: frozenset[str] = frozenset(
    {
        "annotated_diff_content",
        "diff_content",
        "section_diff_content",
    }
)


@semantic_rule(
    name="undefined-bash-placeholder",
    description=(
        "A SKILL.md bash block uses a {placeholder} that is not declared as an ingredient "
        "or assigned as a shell variable. The model will guess the value from ambient context."
    ),
)
def _check_undefined_bash_placeholder(ctx: ValidationContext) -> list[RuleFinding]:
    """Fire for any run_skill step whose SKILL.md has undefined bash-block placeholders."""
    from autoskillit.recipe.rules.rules_skill_content import (
        _resolve_skill_md,  # noqa: PLC0415  # lazy import → facade-mediated patchability
    )

    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not skill_cmd:
            continue

        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue

        skill_md = _resolve_skill_md(
            skill_name, project_root=ctx.project_dir, resolver=ctx.skill_resolver
        )
        if skill_md is None:
            continue  # unknown-skill-command rule handles missing skills

        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue  # file deleted or unread between resolution and read
        bash_blocks = extract_bash_blocks(content)
        if not bash_blocks:
            continue

        used = extract_bash_placeholders(bash_blocks)
        if not used:
            continue

        declared = extract_declared_ingredients(content)
        assigned = shell_vars_assigned(bash_blocks)
        defined = declared | assigned
        allowlisted = {name for (sname, name) in _PSEUDOCODE_ALLOWLIST if sname == skill_name}
        undefined = used - defined - allowlisted

        if undefined:
            findings.append(
                make_finding(
                    rule_name="undefined-bash-placeholder",
                    step_name=step_name,
                    message=(
                        f"Skill '{skill_name}' bash block uses undefined {{placeholder}}: "
                        f"{sorted(undefined)}. Declare as ingredient in ## Arguments, or capture "
                        f"at runtime as VARNAME=$(command)."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="output-section-no-markdown-directive",
    description=(
        "A SKILL.md output section is missing the no-markdown directive. "
        "Skills with expected_output_patterns depend on plain-text token emission; "
        "the model may emit **token_name** = value if not explicitly instructed otherwise."
    ),
)
def _check_output_section_no_markdown_directive(ctx: ValidationContext) -> list[RuleFinding]:
    """Verify that SKILL.md output sections contain an explicit no-markdown directive.

    Skills with expected_output_patterns depend on the model emitting plain-text
    token names. If the SKILL.md does not explicitly prohibit markdown formatting,
    the model may emit **token_name** = value, causing adjudicated_failure.
    """
    from autoskillit.recipe.rules.rules_skill_content import (
        _resolve_skill_md,  # noqa: PLC0415  # lazy import → facade-mediated patchability
        load_bundled_manifest,  # noqa: PLC0415  # lazy import → facade-mediated patchability
    )

    manifest = load_bundled_manifest()
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not skill_cmd:
            continue

        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue

        skill_data = manifest.get("skills", {}).get(skill_name)
        if not skill_data or not skill_data.get("expected_output_patterns"):
            continue  # Only check skills that have contracts with patterns

        skill_md = _resolve_skill_md(
            skill_name, project_root=ctx.project_dir, resolver=ctx.skill_resolver
        )
        if skill_md is None:
            continue  # unknown-skill-command rule handles missing skills

        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue  # file deleted or unread between resolution and read

        output_section_match = re.search(
            r"##\s+Output\b(.+?)(?:^##|\Z)", content, re.DOTALL | re.MULTILINE
        )
        if not output_section_match:
            continue  # No output section — other rules handle this

        output_section = output_section_match.group(1)

        if not _NO_MARKDOWN_DIRECTIVE_PATTERN.search(output_section):
            findings.append(
                make_finding(
                    rule_name="output-section-no-markdown-directive",
                    step_name=step_name,
                    message=(
                        f"SKILL.md for '{skill_name}' has expected_output_patterns but its "
                        f"## Output section does not contain an explicit no-markdown directive. "
                        f"Add: 'Emit the structured output tokens as literal plain text with no "
                        f"markdown formatting on the token names.'"
                    ),
                )
            )
    return findings


@semantic_rule(
    name="executable-field-content-validity",
    description=(
        "Skills with validation rules for executable fields (acquisition, spec_path) "
        "must include content-validity checks, not just presence checks. "
        "Fires when a V-rule block mentions an executable field but contains no "
        "placeholder/template rejection language."
    ),
    severity=Severity.WARNING,
)
def _check_executable_field_content_validity(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    from autoskillit.recipe.rules.rules_skill_content import (
        _resolve_skill_md,  # noqa: PLC0415  # lazy import → facade-mediated patchability
    )

    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not skill_cmd:
            continue
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue
        if skill_name not in _EXECUTABLE_FIELD_SKILLS:
            continue
        skill_md = _resolve_skill_md(
            skill_name, project_root=ctx.project_dir, resolver=ctx.skill_resolver
        )
        if skill_md is None:
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue

        for m in _VRULE_RE.finditer(content):
            block = m.group(0)
            block_lower = block.lower()
            if "acquisition" not in block_lower and "spec_path" not in block_lower:
                continue
            if not _CONTENT_VALIDITY_SIGNALS_RE.search(block):
                findings.append(
                    make_finding(
                        rule_name="executable-field-content-validity",
                        step_name=step_name,
                        message=(
                            f"V-rule {m.group(1)} in {skill_name} mentions an executable "
                            f"field but lacks content-validity criteria "
                            f"(placeholder/template rejection language)."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="source-attribution-directive",
    description=(
        "A SKILL.md for a skill with source_pin_fields lacks explicit prohibition "
        "language against using prohibited sources for pinned fields. Without it, "
        "weaker providers may conflate issue metadata with plan-derived content."
    ),
)
def _check_source_attribution_directive(ctx: ValidationContext) -> list[RuleFinding]:
    from autoskillit.recipe.rules.rules_skill_content import (
        _resolve_skill_md,  # noqa: PLC0415  # lazy import → facade-mediated patchability
        load_bundled_manifest,  # noqa: PLC0415  # lazy import → facade-mediated patchability
    )

    manifest = load_bundled_manifest()
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not skill_cmd:
            continue

        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue

        skill_data = manifest.get("skills", {}).get(skill_name)
        if not skill_data or not skill_data.get("source_pin_fields"):
            continue

        skill_md = _resolve_skill_md(
            skill_name, project_root=ctx.project_dir, resolver=ctx.skill_resolver
        )
        if skill_md is None:
            continue

        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue

        if not _SOURCE_PROHIBITION_RE.search(content):
            findings.append(
                make_finding(
                    rule_name="source-attribution-directive",
                    step_name=step_name,
                    message=(
                        f"SKILL.md for '{skill_name}' has source_pin_fields but lacks "
                        f"explicit prohibition language against using prohibited sources "
                        f"for pinned fields. Add a NEVER item prohibiting the use of "
                        f"issue metadata for task_title derivation."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="inline-content-in-subagent-prompt",
    description=(
        "A SKILL.md blockquoted subagent prompt references a banned *_content "
        "variable. Subagent prompts must use *_path variables and instruct the "
        "subagent to Read the file, per the inline-content-in-subagent-prompt "
        "architectural rule (PR #3651)."
    ),
)
def _check_inline_content_in_subagent_prompt(ctx: ValidationContext) -> list[RuleFinding]:
    """Fire WARNING when a run_skill step's SKILL.md uses banned *_content placeholders.

    Detection scans blockquote subagent prompts for ``{*_content}`` variables
    that are never defined as ingredients. The naming convention is wrong:
    subagent prompts must reference content by PATH (``*_path``) with the
    subagent reading the file. Inline content placeholders are dangling and
    will be silently dropped by the recipe framework, leaving the subagent
    prompt incomplete.
    """
    from autoskillit.recipe.rules.rules_skill_content import (
        _resolve_skill_md,  # noqa: PLC0415  # lazy import → facade-mediated patchability
    )

    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not skill_cmd:
            continue
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue
        skill_md = _resolve_skill_md(
            skill_name, project_root=ctx.project_dir, resolver=ctx.skill_resolver
        )
        if skill_md is None:
            continue  # unknown-skill-command rule handles missing skills
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue  # file deleted or unread between resolution and read

        for step_context, block_text in extract_blockquote_sections(content):
            banned_found = extract_blockquote_placeholders(block_text) & _BANNED_BLOCKQUOTE_VARS
            if not banned_found:
                continue
            for banned_var in sorted(banned_found):
                context_label = f"step {step_context!r}" if step_context else "no step heading"
                findings.append(
                    make_finding(
                        rule_name="inline-content-in-subagent-prompt",
                        step_name=step_name,
                        message=(
                            f"Skill '{skill_name}' blockquote subagent prompt in {context_label} "
                            f"references banned {{placeholder}} {{{banned_var}}}. "
                            f"Subagent prompts must use a *_path variable (e.g., "
                            f"{{{banned_var.removesuffix('_content')}_path}}) and instruct the "
                            f"subagent to Read the file. Inline content placeholders are "
                            f"never populated and produce incomplete prompts."
                        ),
                    )
                )
    return findings
