"""Semantic rules for run_cmd echo-capture alignment in recipe steps."""

from __future__ import annotations

from pathlib import Path

import regex as re

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
        if re.match(r"^\s*bash\s+/\S+\.sh\b", cmd):
            continue
        for cap_key, cap_val in step.capture.items():
            m = RESULT_CAPTURE_RE.search(cap_val.from_)
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
    name="run-cmd-unbundled-script-ref",
    description=(
        "run_cmd step references scripts via relative path — must use {{AUTOSKILLIT_SCRIPTS}}"
    ),
    severity=Severity.ERROR,
)
def _check_unbundled_script_ref(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = (step.with_args or {}).get("cmd", "")
        if not isinstance(cmd, str):
            continue
        if re.match(r"^\s*bash\s+scripts/", cmd):
            findings.append(
                RuleFinding(
                    rule="run-cmd-unbundled-script-ref",
                    severity=Severity.ERROR,
                    step_name=name,
                    message=(
                        f"Step '{name}' uses a relative scripts/ path in cmd. "
                        "Use {{{{AUTOSKILLIT_SCRIPTS}}}}/script_name.sh instead — "
                        "relative paths only resolve in the dev source tree."
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


_SCRIPT_EXISTS_RE = re.compile(r"^\s*bash\s+(/\S+\.sh)\b")


@semantic_rule(
    name="run-cmd-script-exists",
    description=(
        "For every run_cmd step with `bash /path/to/script.sh`, "
        "verify the script file exists on disk."
    ),
    severity=Severity.ERROR,
)
def _check_run_cmd_script_exists(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = (step.with_args or {}).get("cmd", "")
        if not isinstance(cmd, str):
            continue
        m = _SCRIPT_EXISTS_RE.match(cmd)
        if m is None:
            continue
        script_path = Path(m.group(1))
        if not script_path.is_file():
            findings.append(
                RuleFinding(
                    rule="run-cmd-script-exists",
                    severity=Severity.ERROR,
                    step_name=name,
                    message=f"Step '{name}' runs bash {m.group(1)} but the script does not exist.",
                )
            )
    return findings


_BARE_REBASE_RE = re.compile(r"\bgit\b[^\n]*\brebase\b(?!\s+--abort)")

_CONFLICT_SKILL = "resolve-merge-conflicts"


def _has_conflict_routing(step, recipe) -> bool:
    """Check if a step routes to conflict resolution via on_failure or on_result."""
    from collections import deque

    targets: list[str] = []
    if step.on_failure:
        targets.append(step.on_failure)
    if step.on_result:
        for cond in step.on_result.conditions or []:
            if cond.route:
                targets.append(cond.route)

    for target_name in targets:
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(target_name, 0)])
        while queue:
            name, hops = queue.popleft()
            if name in visited or hops > 6:
                continue
            visited.add(name)
            target_step = recipe.steps.get(name)
            if target_step is None:
                continue
            if target_step.tool == "run_skill":
                cmd = (target_step.with_args or {}).get("skill_command", "")
                if _CONFLICT_SKILL in cmd:
                    return True
            if target_step.tool == "run_python":
                callable_str = (target_step.with_args or {}).get("callable", "")
                if "rebase" in callable_str:
                    return True
            if target_step.on_success:
                queue.append((target_step.on_success, hops + 1))
            if target_step.on_failure:
                queue.append((target_step.on_failure, hops + 1))
            if target_step.on_result:
                for cond in target_step.on_result.conditions or []:
                    if cond.route:
                        queue.append((cond.route, hops + 1))
    return False


@semantic_rule(
    name="run-cmd-bare-rebase-without-conflict-routing",
    description=(
        "run_cmd step performs git rebase but routes on_failure to a terminal "
        "without conflict resolution. Use run_python with a rebase callable instead."
    ),
    severity=Severity.ERROR,
)
def _check_run_cmd_bare_rebase(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = (step.with_args or {}).get("cmd", "")
        if not isinstance(cmd, str):
            continue
        if not _BARE_REBASE_RE.search(cmd):
            continue
        if _has_conflict_routing(step, ctx.recipe):
            continue
        findings.append(
            RuleFinding(
                rule="run-cmd-bare-rebase-without-conflict-routing",
                severity=Severity.ERROR,
                step_name=name,
                message=(
                    f"Step '{name}' performs a bare git rebase via run_cmd but does not "
                    "route failures to conflict resolution (resolve-merge-conflicts). "
                    "Use run_python with a rebase callable that aborts on conflict and "
                    "route the result to a resolve-merge-conflicts skill step."
                ),
            )
        )
    return findings


_NONEMPTY_GUARD_RE = re.compile(r"(?:test\s+-s\s+|\[\s+-s\s+)")


@semantic_rule(
    name="run-cmd-path-capture-requires-nonempty-guard",
    description=(
        "run_cmd step echoes a path-typed capture without a test -s or [ -s "
        "non-empty file guard in the cmd. A command that writes to a file via "
        "redirect (>) and echoes the path can silently produce a 0-byte file."
    ),
    severity=Severity.WARNING,
)
def _check_run_cmd_path_capture_nonempty_guard(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = (step.with_args or {}).get("cmd", "")
        if not isinstance(cmd, str):
            continue
        has_path_capture = any(
            entry.value_type == "path"
            for entry in (*step.capture.values(), *step.capture_list.values())
        )
        if not has_path_capture:
            continue
        if not _NONEMPTY_GUARD_RE.search(cmd):
            findings.append(
                RuleFinding(
                    rule="run-cmd-path-capture-requires-nonempty-guard",
                    severity=Severity.WARNING,
                    step_name=name,
                    message=(
                        f"Step '{name}' captures a path-typed value but cmd "
                        "does not contain a 'test -s' or '[ -s' non-empty "
                        'file guard. Add `test -s "$FILE" &&` before the '
                        "echo statement to prevent emitting a 0-byte file path."
                    ),
                )
            )
    return findings
