"""Semantic rules for CI polling patterns in recipe steps."""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_NO_RUNS_RE = re.compile(r"""==\s*['"]?no_runs['"]?""")
_TIMED_OUT_RE = re.compile(r"""==\s*['"]?timed_out['"]?""")


@semantic_rule(
    name="ci-polling-inline-shell",
    description="Flags run_cmd steps containing gh run commands — use wait_for_ci tool instead",
    severity=Severity.WARNING,
)
def _check_ci_polling_inline_shell(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = (step.with_args or {}).get("cmd", "")
        if not isinstance(cmd, str):
            continue
        if "gh run watch" in cmd or "gh run list" in cmd:
            findings.append(
                RuleFinding(
                    rule="ci-polling-inline-shell",
                    severity=Severity.WARNING,
                    step_name=name,
                    message=(
                        f"Step '{name}' uses inline 'gh run' commands in run_cmd. "
                        "Use the wait_for_ci MCP tool instead for race-immune CI watching "
                        "with structured output."
                    ),
                )
            )
        if "gh pr view" in cmd and (
            "statusCheckRollup" in cmd or "--json checks" in cmd or ",checks" in cmd
        ):
            findings.append(
                RuleFinding(
                    rule="ci-polling-inline-shell",
                    severity=Severity.WARNING,
                    step_name=name,
                    message=(
                        f"Step '{name}' uses inline 'gh pr view --json statusCheckRollup' "
                        "for CI polling. Use the wait_for_ci MCP tool instead for "
                        "race-immune CI watching with structured output."
                    ),
                )
            )
        if "gh api" in cmd and ("/status" in cmd or "/statuses" in cmd or "check-runs" in cmd):
            findings.append(
                RuleFinding(
                    rule="ci-polling-inline-shell",
                    severity=Severity.WARNING,
                    step_name=name,
                    message=(
                        f"Step '{name}' uses inline 'gh api' for CI status polling. "
                        "Use the wait_for_ci MCP tool instead for race-immune CI watching "
                        "with structured output."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="ci-no-runs-unguarded",
    description=(
        "Flags wait_for_ci steps that use bare on_success routing without "
        "on_result conditions that intercept conclusion='no_runs'"
    ),
    severity=Severity.ERROR,
)
def _check_ci_no_runs_unguarded(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_ci":
            continue
        has_no_runs_guard = False
        if step.on_result and step.on_result.conditions:
            has_explicit_no_runs = any(
                c.when and _NO_RUNS_RE.search(c.when) for c in step.on_result.conditions
            )
            has_catch_all = any(not c.when for c in step.on_result.conditions)
            has_no_runs_guard = has_explicit_no_runs or has_catch_all
        if has_no_runs_guard:
            continue
        if step.on_success or step.on_result:
            target = step.on_success or "on_result routing"
            findings.append(
                RuleFinding(
                    rule="ci-no-runs-unguarded",
                    severity=Severity.ERROR,
                    step_name=name,
                    message=(
                        f"Step '{name}' uses wait_for_ci without an on_result condition "
                        "that intercepts conclusion='no_runs'. wait_for_ci returns "
                        "conclusion='no_runs' on the success path — add on_result "
                        f"conditions to intercept no_runs before routing to '{target}'."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="ci-timed-out-unguarded",
    description=(
        "Flags wait_for_ci steps whose on_result routing lacks an explicit "
        "condition for conclusion='timed_out'. A catch-all arm is NOT "
        "sufficient — timed_out means CI is still running, not failed."
    ),
    severity=Severity.ERROR,
)
def _check_ci_timed_out_unguarded(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_ci":
            continue
        if not (step.on_success or step.on_result):
            continue
        has_explicit_timed_out = False
        if step.on_result and step.on_result.conditions:
            has_explicit_timed_out = any(
                c.when and _TIMED_OUT_RE.search(c.when) for c in step.on_result.conditions
            )
        if has_explicit_timed_out:
            continue
        target = step.on_success or "on_result routing"
        findings.append(
            RuleFinding(
                rule="ci-timed-out-unguarded",
                severity=Severity.ERROR,
                step_name=name,
                message=(
                    f"Step '{name}' uses wait_for_ci without an on_result condition "
                    "that intercepts conclusion='timed_out'. timed_out means CI is "
                    "still in progress — routing it through a catch-all to the CI "
                    f"failure path is semantically wrong. Add an explicit timed_out "
                    f"arm before '{target}'."
                ),
            )
        )
    return findings


_CI_EVENT_SCOPE_TOOLS = {"wait_for_ci", "get_ci_status"}


@semantic_rule(
    name="ci-missing-event-scope",
    description=(
        "CI tool step without event parameter causes silent run exclusion on feature branches"
    ),
    severity=Severity.ERROR,
)
def _check_ci_missing_event_scope(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool not in _CI_EVENT_SCOPE_TOOLS:
            continue
        if "event" not in (step.with_args or {}):
            findings.append(
                RuleFinding(
                    rule="ci-missing-event-scope",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' calls {step.tool} without an 'event' parameter. "
                        f"On feature branches excluded from push triggers, the push-scoped "
                        f"filter returns no runs even when pull_request CI is active, causing "
                        f"no_runs timeout. Add event: '${{{{ context.ci_event }}}}' "
                        f"(requires check_repo_ci_event to run first) "
                        f"or set ci.event in project config."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="ci-hardcoded-workflow",
    description="wait_for_ci step with hardcoded workflow bypasses config fallback",
    severity=Severity.WARNING,
)
def _check_ci_hardcoded_workflow(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_ci":
            continue
        workflow = (step.with_args or {}).get("workflow")
        if isinstance(workflow, str) and not workflow.startswith("${{"):
            findings.append(
                RuleFinding(
                    rule="ci-hardcoded-workflow",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' hardcodes workflow: '{workflow}'. "
                        f"Remove the workflow parameter to use the project-level "
                        f"ci.workflow config default, or use '${{{{ inputs.workflow }}}}' "
                        f"to parameterize it via recipe ingredients."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="ci-event-literal-merge-group",
    description=(
        "Flags wait_for_ci steps that hardcode event='merge_group' — use context.ci_event instead"
    ),
    severity=Severity.ERROR,
)
def _check_ci_event_literal_merge_group(ctx: ValidationContext) -> list[RuleFinding]:
    """Flag wait_for_ci steps that hardcode event='merge_group'."""
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_ci":
            continue
        event_value = (step.with_args or {}).get("event")
        if event_value == "merge_group":
            findings.append(
                RuleFinding(
                    rule="ci-event-literal-merge-group",
                    severity=Severity.ERROR,
                    step_name=name,
                    message=(
                        "wait_for_ci must not hardcode event='merge_group'. "
                        "Use context.ci_event (which is 'push' or null) for pre-queue CI, "
                        "or add a lifecycle-aware condition for in-queue CI."
                    ),
                )
            )
    return findings


_CI_TIMEOUT_MINIMUM = 600


@semantic_rule(
    name="ci-timeout-minimum",
    description="Flags wait_for_ci steps with timeout_seconds below 600",
    severity=Severity.WARNING,
)
def _check_ci_timeout_minimum(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_ci":
            continue
        raw = (step.with_args or {}).get("timeout_seconds")
        if raw is None:
            continue
        try:
            timeout = int(raw)
        except (ValueError, TypeError):
            continue
        if timeout < _CI_TIMEOUT_MINIMUM:
            findings.append(
                RuleFinding(
                    rule="ci-timeout-minimum",
                    severity=Severity.WARNING,
                    step_name=name,
                    message=(
                        f"timeout_seconds={timeout} is below the {_CI_TIMEOUT_MINIMUM}s "
                        f"minimum — average CI duration is ~317s with peaks to 392s."
                    ),
                )
            )
    return findings
