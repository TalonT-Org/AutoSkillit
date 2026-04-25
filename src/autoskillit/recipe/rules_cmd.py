"""Semantic rules for run_cmd echo-capture alignment in recipe steps."""

from __future__ import annotations

import re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._git_helpers import _GIT_REMOTE_COMMAND_RE, _LITERAL_ORIGIN_RE
from autoskillit.recipe.contracts import RESULT_CAPTURE_RE
from autoskillit.recipe.registry import RuleFinding, semantic_rule

# Raw tool output fields — these are populated directly from the tool JSON response,
# no echo statement in the cmd is required to capture them.
_RAW_RESULT_FIELDS = {"stdout", "stderr", "exit_code"}

# Matches find ... | sort ... | (tail|head) patterns that indicate a step is
# re-discovering a path that should have been captured by an upstream step.
_FIND_HEURISTIC_RE = re.compile(r"\bfind\b.+\|\s*sort\b.+\|\s*(tail|head)\b")


@semantic_rule(
    name="run-cmd-emit-alignment",
    description=(
        "For every run_cmd step, each non-raw capture key K must have a matching "
        'echo "K=..." in the cmd. A missing echo causes a silent empty-string capture.'
    ),
    severity=Severity.ERROR,
)
def _check_run_cmd_emit_alignment(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = (step.with_args or {}).get("cmd", "")
        if not isinstance(cmd, str):
            continue
        for cap_key, cap_val in step.capture.items():
            m = RESULT_CAPTURE_RE.search(cap_val)
            if m is None:
                # Cannot determine the result field — skip (e.g. pipe-filtered values).
                continue
            result_key = m.group(1)
            if result_key in _RAW_RESULT_FIELDS:
                continue
            # Check that the cmd emits `echo "result_key=..."`.
            echo_pattern = re.compile(rf'\becho\s+"?{re.escape(result_key)}=')
            if not echo_pattern.search(cmd):
                findings.append(
                    RuleFinding(
                        rule="run-cmd-emit-alignment",
                        severity=Severity.ERROR,
                        step_name=name,
                        message=(
                            f"Step '{name}' captures '{cap_key}' from result.{result_key} "
                            f'but cmd contains no `echo "{result_key}=..."` statement. '
                            f'Add `echo "{result_key}=${{...}}"` to the cmd or the '
                            "captured value will always be empty."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="run-cmd-find-rediscovery",
    description=(
        "Flags run_cmd steps using find|sort|tail/head to select a path — this pattern "
        "indicates an upstream step computed the path but did not echo it into context."
    ),
    severity=Severity.WARNING,
)
def _check_run_cmd_find_rediscovery(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = (step.with_args or {}).get("cmd", "")
        if not isinstance(cmd, str):
            continue
        if _FIND_HEURISTIC_RE.search(cmd):
            findings.append(
                RuleFinding(
                    rule="run-cmd-find-rediscovery",
                    severity=Severity.WARNING,
                    step_name=name,
                    message=(
                        f"Step '{name}' uses a `find | sort | tail` heuristic to select "
                        "a directory. This pattern indicates an upstream step computed "
                        "the path but did not echo it into context. Capture the path via "
                        "echo+capture in the originating step instead."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="hardcoded-origin-in-run-cmd",
    description="run_cmd step uses hardcoded 'origin' remote name",
    severity=Severity.WARNING,
)
def _check_hardcoded_origin_in_run_cmd(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = (step.with_args or {}).get("cmd", "")
        if not isinstance(cmd, str):
            continue
        if "git remote set-url origin" in cmd:
            continue
        for line in cmd.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if _GIT_REMOTE_COMMAND_RE.search(stripped) and _LITERAL_ORIGIN_RE.search(stripped):
                findings.append(
                    RuleFinding(
                        rule="hardcoded-origin-in-run-cmd",
                        severity=Severity.WARNING,
                        step_name=name,
                        message=(
                            f"Step '{name}' uses hardcoded 'origin' in a git command. "
                            "In clone-isolated pipelines, origin is file://<clone_path>. "
                            "Use: REMOTE=$(git remote get-url upstream >/dev/null 2>&1 "
                            "&& echo upstream || echo origin)"
                        ),
                    )
                )
    return findings
