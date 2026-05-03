"""Semantic rules for workspace isolation — prevent recipes from operating on the source repo."""

from __future__ import annotations

import re

from autoskillit.core import Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import INPUT_REF_RE
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)

# Git commands that mutate repository state
_GIT_MUTATION_RE = re.compile(
    r"git\s+(checkout|worktree\s+add|branch\s+-[dD]|push|reset|merge|rebase|commit|cherry-pick)"
)

# Tools that inherently mutate git state when given a cwd
_GIT_MUTATING_TOOLS = frozenset({"create_unique_branch"})


@semantic_rule(
    name="source-isolation-violation",
    description=(
        "Steps using git-mutating tools or run_skill must not operate on the source repo "
        "via inputs.* as cwd. Use clone_repo and operate on context.work_dir instead."
    ),
    severity=Severity.ERROR,
)
def _check_source_isolation(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    # NOTE: structural presence check — does not account for skip_when_false.
    # A conditionally-skipped clone_repo step will suppress the WARNING below
    # even for execution paths where the clone never runs.  Full fix requires
    # dataflow-aware conditional reachability analysis (out of scope).
    has_clone = any(step.tool == "clone_repo" for step in wf.steps.values())
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        cwd = step.with_args.get("cwd", "")
        if not INPUT_REF_RE.search(cwd):
            continue

        # Rule 1: git-mutating tools with inputs.* cwd — always ERROR
        if step.tool in _GIT_MUTATING_TOOLS:
            findings.append(
                RuleFinding(
                    rule="source-isolation-violation",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' uses '{step.tool}' with cwd pointing at the "
                        f"source repo ({cwd}). This tool mutates git state. "
                        f"Use clone_repo and operate on context.work_dir instead."
                    ),
                )
            )

        # Rule 1 extension: run_skill with inputs.* cwd and no clone — WARNING
        if step.tool == "run_skill" and not has_clone:
            findings.append(
                RuleFinding(
                    rule="source-isolation-violation",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' runs a skill with cwd pointing at the source "
                        f"repo ({cwd}) and the recipe has no clone_repo step. "
                        f"Skills modify files — operating on the source repo without "
                        f"clone isolation is unsafe."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="git-mutation-on-source",
    description=("run_cmd steps with git-mutating commands must not use inputs.* as cwd."),
    severity=Severity.WARNING,
)
def _check_git_mutation_on_source(ctx: ValidationContext) -> list[RuleFinding]:
    wf = ctx.recipe
    findings: list[RuleFinding] = []
    for step_name, step in wf.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = step.with_args.get("cmd", "")
        cwd = step.with_args.get("cwd", "")
        if not INPUT_REF_RE.search(cwd):
            continue
        if not _GIT_MUTATION_RE.search(cmd):
            continue
        findings.append(
            RuleFinding(
                rule="git-mutation-on-source",
                severity=Severity.WARNING,
                step_name=step_name,
                message=(
                    f"Step '{step_name}' runs a git-mutating command with cwd pointing "
                    f"at the source repo ({cwd}). "
                    f"Use clone_repo and operate on context.work_dir instead."
                ),
            )
        )
    return findings
