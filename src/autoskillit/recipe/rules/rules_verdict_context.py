"""Verdict-context precondition rule.

Fires ERROR when a ``run_skill`` step invokes a skill whose ``allowed_values``
contains a CI-context-dependent verdict (``ci_only_failure``) and the step's
``skill_command`` does NOT reference any CI context variable (``diagnosis_path``
or ``ci_conclusion`` or equivalent), AND the step routes ``ci_only_failure`` to
an escalation/failure target.

Without CI context, the skill cannot possibly emit ``ci_only_failure`` (which
requires knowing the CI diagnosis). Routing it to escalation makes the verdict
semantically impossible — a terminal kill on a verdict that can never fire.
"""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity, get_logger, resolve_skill_name
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_helpers import get_allowed_values_for_skill
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

logger = get_logger(__name__)

_CI_CONTEXT_DEPENDENT_VERDICTS = frozenset({"ci_only_failure"})

_CI_CONTEXT_VAR_PATTERN = re.compile(
    r"(?:\{\{.*?)?context\.(?:diagnosis_path|ci_conclusion|merge_gate_ci_conclusion"
    r"|merge_gate_diagnosis_path|ci_failed_jobs)"
)

_ESCALATION_KEYWORDS = ("failure", "escalat", "stop")


def _classify_route_target(target: str) -> str:
    """Classify a route target as 'escalation' or 'continuation'.

    Inlined here (not imported from rules_verdict.py) because rule modules
    must not cross-import per rules/AGENTS.md architecture constraint.
    """
    if any(kw in target for kw in _ESCALATION_KEYWORDS):
        return "escalation"
    return "continuation"


@semantic_rule(
    name="verdict-context-precondition",
    description=(
        "A run_skill step invoking a skill whose allowed_values contains a CI-context-"
        "dependent verdict (ci_only_failure) must reference CI context (diagnosis_path, "
        "ci_conclusion, or equivalent) in its skill_command AND must not route that "
        "verdict to escalation without that context — otherwise the verdict is "
        "semantically impossible to emit and the escalation route is dead."
    ),
    severity=Severity.ERROR,
)
def _check_verdict_context_precondition(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_command = str((step.with_args or {}).get("skill_command") or "")
        skill_name = resolve_skill_name(skill_command)
        if not skill_name:
            continue

        allowed_by_output = get_allowed_values_for_skill(skill_name)
        if not allowed_by_output:
            continue

        ci_context_values = set()
        for _output, values in allowed_by_output.items():
            for v in values:
                if v in _CI_CONTEXT_DEPENDENT_VERDICTS:
                    ci_context_values.add(v)
        if not ci_context_values:
            continue

        if _CI_CONTEXT_VAR_PATTERN.search(skill_command):
            continue

        if not step.on_result or not step.on_result.conditions:
            continue

        for condition in step.on_result.conditions:
            when = condition.when
            if not when or when.strip() == "true":
                continue
            for value in ci_context_values:
                if re.search(r"\b" + re.escape(value) + r"\b", when):
                    classification = _classify_route_target(condition.route or "")
                    if classification == "continuation":
                        continue
                    findings.append(
                        make_finding(
                            rule_name="verdict-context-precondition",
                            step_name=step_name,
                            message=(
                                f"Step '{step_name}' invokes '{skill_name}' whose "
                                f"allowed_values contains '{value}' (a CI-context-dependent "
                                f"verdict), but the skill_command does NOT reference any "
                                f"CI context variable (diagnosis_path, ci_conclusion, "
                                f"merge_gate_ci_conclusion, merge_gate_diagnosis_path, or "
                                f"ci_failed_jobs). Without CI context, the skill cannot emit "
                                f"'{value}', yet the step routes it to escalation target "
                                f"'{condition.route}'. Add CI context to skill_command "
                                f"(e.g., pass 'ci_conclusion: ${{{{ context.ci_conclusion }}}}') "
                                f"or route '{value}' to a non-escalation target."
                            ),
                        )
                    )

    return findings
