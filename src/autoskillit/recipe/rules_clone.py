"""Semantic validation rules for clone, push, and multipart-plan capture.

clone-step dataflow rules
--------------------------
- push-missing-explicit-remote-url: push_to_remote step is missing an explicit
  remote_url argument; implicit lookup fails for non-bare repos.
- clone-local-strategy-with-remote-url-capture: a run_python clone_repo step
  uses strategy="clone_local" and captures remote_url from the result. Under
  local transport, remote_url is always empty, so any downstream consumer of
  context.remote_url will receive "". The rule fires at recipe-validation time
  (open_kitchen / load_recipe) before any runtime call.
"""

from __future__ import annotations

import re

from autoskillit.core import (
    SKILL_COMMAND_PREFIX,
    SKILL_TOOLS,
    Severity,
    get_logger,
)
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)


@semantic_rule(
    name="multipart-plan-parts-not-captured",
    description="Multi-part plan recipes must capture plan_parts via capture_list.",
    severity=Severity.ERROR,
)
def _check_plan_parts_captured(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
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
    name="skill-command-missing-prefix",
    description="run_skill step has a skill_command that does not start with '/'",
    severity=Severity.WARNING,
)
def _check_skill_command_prefix(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings = []
    for step_name, step in wf.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = step.with_args.get("skill_command")
        if skill_cmd is None:
            continue  # absent key — fail-open
        if not skill_cmd.strip().startswith(SKILL_COMMAND_PREFIX):
            findings.append(
                RuleFinding(
                    rule="skill-command-missing-prefix",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"skill_command {skill_cmd!r} does not start with '/'. "
                        "run_skill requires a slash-prefix (e.g. /autoskillit:investigate). "
                        "Prose prompts bypass the skill contract and run with "
                        "--dangerously-skip-permissions."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="push-missing-explicit-remote-url",
    description="push_to_remote missing remote_url; implicit lookup fails for non-bare repos",
    severity=Severity.WARNING,
)
def _check_push_missing_explicit_remote_url(ctx: ValidationContext) -> list[RuleFinding]:
    recipe = ctx.recipe
    return [
        RuleFinding("push-missing-explicit-remote-url", Severity.WARNING, n, "missing remote_url")
        for n, step in recipe.steps.items()
        if step.tool == "push_to_remote" and "remote_url" not in (step.with_args or {})
    ]


_CLONE_REPO_PYTHON = "autoskillit.workspace.clone.clone_repo"
_TEMPLATE_RE = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)
_SAFE_STRATEGIES = {"", "proceed"}


def _captures_result_remote_url(capture: dict[str, str]) -> bool:
    """Return True if any capture entry reads result.remote_url from the step result."""
    for tmpl in capture.values():
        # Only flag entries whose template expression references result.remote_url.
        # An entry like `other_key: "${{ result.remote_url }}"` still captures the
        # empty value, so it is equally dangerous.
        if "result.remote_url" in tmpl:
            return True
    return False


@semantic_rule(
    name="clone-local-strategy-with-remote-url-capture",
    description=(
        "clone_repo step using strategy='clone_local' must not capture remote_url; "
        "under local transport remote_url is always empty"
    ),
    severity=Severity.ERROR,
)
def _check_clone_local_remote_url_capture(ctx: ValidationContext) -> list[RuleFinding]:
    """Reject recipes that combine clone_repo(strategy='clone_local') with remote_url capture.

    Under the clone_local strategy, clone_repo always sets remote_url to "".  Any
    downstream step that reads context.remote_url therefore receives an empty string,
    silently corrupting push, CI-wait, or run_cmd steps.

    Strategy classification
    -----------------------
    - absent / "" / "proceed"    → safe; Part A guarantees non-empty on success
    - "clone_local" (literal)    → ERROR  (always empty)
    - template "${{ ... }}"      → WARNING (cannot determine statically)
    - any other unknown literal  → WARNING (conservative default)

    The rule inspects template *expressions* (capture values), not just capture key
    names, so aliasing (e.g. ``alt_url: "${{ result.remote_url }}"``) is caught.
    """
    recipe = ctx.recipe
    findings: list[RuleFinding] = []

    for step_name, step in recipe.steps.items():
        if step.tool != "run_python":
            continue
        if step.python != _CLONE_REPO_PYTHON:
            continue

        capture = step.capture or {}
        if not _captures_result_remote_url(capture):
            continue

        strategy: str = (step.with_args or {}).get("strategy", "")

        if strategy in _SAFE_STRATEGIES:
            # proceed / absent: Part A guarantees a non-empty remote_url on success.
            continue

        if strategy == "clone_local":
            severity = Severity.ERROR
            explanation = (
                f'clone step "{step_name}" uses strategy="clone_local" and captures '
                f"remote_url from result. Under clone_local, remote_url is always "
                f'empty, so downstream consumers of context.remote_url will receive "". '
                f'Remove the remote_url capture, or use strategy="proceed" if a '
                f"network clone is intended."
            )
        elif _TEMPLATE_RE.search(strategy):
            severity = Severity.WARNING
            explanation = (
                f'clone step "{step_name}" uses a templated strategy ({strategy!r}) '
                f"that cannot be statically determined. If the strategy resolves to "
                f'"clone_local" at runtime, remote_url will be empty and downstream '
                f"consumers of context.remote_url will receive an empty string."
            )
        else:
            severity = Severity.WARNING
            explanation = (
                f'clone step "{step_name}" uses an unrecognised strategy ({strategy!r}) '
                f"and captures remote_url. If this strategy produces an empty remote_url, "
                f'downstream consumers of context.remote_url will receive "". '
                f"Verify that {strategy!r} always produces a non-empty remote_url, "
                f"or remove the remote_url capture."
            )

        findings.append(
            RuleFinding(
                rule="clone-local-strategy-with-remote-url-capture",
                severity=severity,
                step_name=step_name,
                message=explanation,
            )
        )

    return findings
