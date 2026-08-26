"""SKILL.md content-structure semantic rules.

Family of @semantic_rule checks that scan SKILL.md sections for structural
issues that affect model behavior — currently the
`transition-boundary-anti-confirmation` rule, which detects unprotected
phase/group/batch transition boundaries.

`_resolve_skill_md` is NOT imported at module scope. The rule body that
calls it performs a function-body-scoped lazy import against
`autoskillit.recipe.rules.rules_skill_content` (the facade) so that
`patch.object(rules_skill_content, "_resolve_skill_md", ...)` continues to
redirect rule-body lookups via the facade namespace.
"""

from __future__ import annotations

import regex as re

from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_placeholder_parser import extract_sections
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

_TRANSITION_BOUNDARY_RES: list[tuple[str, re.Pattern[str]]] = [
    (
        "group-iteration",
        re.compile(
            r"group\s+(?:iteration|N\+1|dispatch|ordering)|between\s+groups"
            r"|merge[- ]wait.*between|verify.*merged.*before.*(?:start|Group)",
            re.IGNORECASE,
        ),
    ),
    (
        "phase-loop",
        re.compile(
            r"(?:phase|step)\s+(?:loop|iteration|repeat)|between\s+phases"
            r"|phase\s+by\s+phase|for\s+each\s+(?:[\w/]+\s+)?phase",
            re.IGNORECASE,
        ),
    ),
    (
        "batch-processing",
        re.compile(
            r"batch\s+(?:N|iteration|processing)|between\s+(?:batches|issues)",
            re.IGNORECASE,
        ),
    ),
]

_TRANSITION_ANTI_CONFIRM_RE: re.Pattern[str] = re.compile(
    r"(?:never|do\s+not|must\s+not|prohibited)"
    r"[^\n]{0,80}"
    r"(?:ask|confirm|pause|AskUserQuestion"
    r"|wait\s+for\s+[^\n]{0,30}(?:user|human|permission|approval|confirmation))"
    r"|(?:proceed|dispatch|continue|start)"
    r"[^\n]{0,60}"
    r"(?:immediately|without[^\n]{0,30}(?:asking|confirming|pausing|waiting))",
    re.IGNORECASE,
)


@semantic_rule(
    name="transition-boundary-anti-confirmation",
    description=(
        "A SKILL.md section describes a transition boundary (between groups, phases, or batches) "
        "but does not contain an explicit anti-confirmation instruction. Without it, the model "
        "may insert unsolicited confirmation prompts between iterations."
    ),
)
def _check_transition_boundary_anti_confirmation(ctx: ValidationContext) -> list[RuleFinding]:
    """Fire for any run_skill step whose SKILL.md has unprotected transition boundaries."""
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
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        unprotected: list[tuple[str, str]] = []
        for section in extract_sections(content):
            lines = section.splitlines()
            heading = lines[0].strip() if lines else ""
            for boundary_name, boundary_re in _TRANSITION_BOUNDARY_RES:
                if boundary_re.search(section) and not _TRANSITION_ANTI_CONFIRM_RE.search(section):
                    unprotected.append((boundary_name, heading))
                    break
        if unprotected:
            boundary_desc = "; ".join(f"'{b}' in section '{h}'" for b, h in unprotected)
            findings.append(
                make_finding(
                    rule_name="transition-boundary-anti-confirmation",
                    step_name=step_name,
                    message=(
                        f"Skill '{skill_name}' has unprotected transition "
                        f"{'boundaries' if len(unprotected) > 1 else 'boundary'}: "
                        f"{boundary_desc}. Add explicit anti-confirmation instruction "
                        f"(e.g., 'NEVER use AskUserQuestion to confirm proceeding')."
                    ),
                )
            )
    return findings
