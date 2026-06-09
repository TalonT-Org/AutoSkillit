"""Semantic rules for run_python callable scoping requirements."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

# Callables that discover files via glob and require a scoped directory argument.
SCOPED_CALLABLES: dict[str, str] = {
    "batch_create_issues": "audit_run_dir",
}


@semantic_rule(
    name="callable-requires-scoped-discovery",
    description=(
        "run_python steps calling file-discovering callables must pass "
        "a scoped directory argument (audit_run_dir or similar) to prevent "
        "cross-run file accumulation."
    ),
    severity=Severity.ERROR,
)
def _check_callable_scoped_discovery(ctx: ValidationContext) -> list[RuleFinding]:
    """Error when a file-discovering callable is called without its required scoping parameter.

    batch_create_issues uses an unscoped glob on the flat validate-audit/ directory.
    Without audit_run_dir, it cannot distinguish current-run files from prior-run files,
    causing duplicate issue creation on re-runs.

    Other callables in SCOPED_CALLABLES follow the same pattern.
    """
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_python":
            continue
        callable_val = str(step.with_args.get("callable", ""))
        callable_leaf = callable_val.rsplit(".", 1)[-1]
        for callable_name, required_key in SCOPED_CALLABLES.items():
            if callable_leaf == callable_name:
                if required_key not in step.with_args:
                    findings.append(
                        make_finding(
                            rule_name="callable-requires-scoped-discovery",
                            step_name=step_name,
                            message=(
                                f"step '{step_name}': {callable_name} call is "
                                f"missing '{required_key}' in with args. Without a "
                                f"scoped directory, the callable will glob all files "
                                f"including those from prior runs."
                            ),
                        )
                    )
    return findings
